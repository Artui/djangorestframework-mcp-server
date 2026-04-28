from __future__ import annotations

from typing import Any

from rest_framework_services.exceptions.service_error import ServiceError

from tests.testapp.models import Invoice


def create_invoice(*, data: dict[str, Any]) -> Invoice:
    """Create a new invoice from validated input data."""
    return Invoice.objects.create(number=data["number"], amount_cents=data["amount_cents"])


def get_invoice(*, pk: int) -> Invoice:
    """Fetch a single invoice by primary key."""
    try:
        return Invoice.objects.get(pk=pk)
    except Invoice.DoesNotExist as exc:
        raise ServiceError(f"Invoice {pk} not found") from exc


def list_invoices() -> list[Invoice]:
    """Return every invoice."""
    return list(Invoice.objects.all())
