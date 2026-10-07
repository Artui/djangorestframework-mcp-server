"""Mutation-shaped callables. Each is registered as a service tool.

Services are pure Python functions — they don't know anything about
MCP. The ``data`` kwarg comes from the validated input serializer; the
return value is rendered through the registered ``output_serializer``.
"""

from __future__ import annotations

from typing import Any

from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError

from invoices.models import Invoice


def create_invoice(*, data: dict[str, Any]) -> Invoice:
    """Create a new invoice. Returns the created instance for serialization."""
    return Invoice.objects.create(
        number=data["number"],
        amount_cents=data["amount_cents"],
    )


def mark_invoice_sent(*, data: dict[str, Any]) -> Invoice:
    """Flip the ``sent`` flag on an existing invoice.

    Raises ``ServiceError`` (mapped to ``-32000`` at the MCP boundary)
    when the invoice doesn't exist or is already sent — both are
    semantic errors, not input-shape errors.
    """
    pk: int = data["pk"]
    try:
        invoice: Invoice = Invoice.objects.get(pk=pk)
    except Invoice.DoesNotExist as exc:
        raise ServiceError(f"Invoice {pk} not found") from exc
    if invoice.sent:
        raise ServiceError(f"Invoice {pk} is already sent")
    invoice.sent = True
    invoice.save(update_fields=["sent"])
    return invoice


def set_invoice_amount(*, instance: Invoice, data: dict[str, Any]) -> Invoice:
    """Set an invoice's amount. ``instance`` is the row ``invoice_by_number`` found.

    Idempotent: the same call twice leaves the invoice as one call did, which
    the spec declares with ``idempotent=True`` and the tool lists as
    ``idempotentHint``.
    """
    instance.amount_cents = data["amount_cents"]
    instance.save(update_fields=["amount_cents"])
    return instance


def apply_credit(*, instance: Invoice, credit_cents: int) -> Invoice | None:
    """Take ``credit_cents`` off an invoice; ``None`` when the credit settles it.

    ``instance`` is the row ``invoice_by_number`` found. There is no input
    serializer: the tool spreads its arguments into this signature, so
    ``credit_cents`` is the tool's own argument, listed in its ``inputSchema``
    and required because it has no default. A credit covering the whole amount
    deletes the invoice and presents nothing, which the spec declares with
    ``allow_none=True``.
    """
    if credit_cents <= 0:
        raise ServiceValidationError({"credit_cents": ["A credit must be positive."]})
    if credit_cents >= instance.amount_cents:
        instance.delete()
        return None
    instance.amount_cents -= credit_cents
    instance.save(update_fields=["amount_cents"])
    return instance
