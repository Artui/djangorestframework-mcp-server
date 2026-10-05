"""Mutation-shaped callables. Each is registered as a service tool.

Services are pure Python functions — they don't know anything about
MCP. The ``data`` kwarg comes from the validated input serializer; the
return value is rendered through the registered ``output_serializer``.
"""

from __future__ import annotations

from typing import Any

from rest_framework_services.exceptions.service_error import ServiceError

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
