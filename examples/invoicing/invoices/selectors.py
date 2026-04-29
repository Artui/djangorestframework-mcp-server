"""Read-shaped callables. Each is registered as either a selector tool or a resource.

Selectors return querysets (for selector tools) or model instances
(for resource reads). They never filter, paginate, or sort — that's
the tool layer's job.
"""

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework_services.exceptions.service_error import ServiceError

from invoices.models import Invoice


def list_invoices() -> QuerySet[Invoice]:
    """Base queryset for ``invoices.list``.

    Real projects usually scope this to the caller — e.g.
    ``Invoice.objects.for_user(user)``. This example is intentionally
    unscoped so the demo data is visible to every caller.
    """
    return Invoice.objects.all()


def get_invoice(*, pk: int) -> Invoice:
    """Single invoice by primary key — backs the ``invoice`` resource."""
    try:
        return Invoice.objects.get(pk=int(pk))
    except Invoice.DoesNotExist as exc:
        raise ServiceError(f"Invoice {pk} not found") from exc
