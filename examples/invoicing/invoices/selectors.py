"""Read-shaped callables. Each is registered as either a selector tool or a resource.

Selectors return querysets (for selector tools) or model instances
(for resource reads). They never filter, paginate, or sort — that's
the tool layer's job.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet, Sum
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


def invoice_by_number(*, number: str) -> QuerySet[Invoice]:
    """The invoice ``invoices.set_amount`` acts on, looked up by its number.

    A service tool's target selector. Its parameters are the tool's lookup, so
    ``number`` is advertised in the tool's ``inputSchema`` beside the input
    serializer's ``amount_cents``. It has no default and nothing on the server
    fills it, so it is required there, and a call without it is answered with a
    ``validation_error`` result naming ``number`` before the lookup runs.
    """
    return Invoice.objects.filter(number=number)


def find_invoice(*, number: str) -> QuerySet[Invoice]:
    """Backs ``invoices.find``, a RETRIEVE that may find nothing.

    Registered with ``allow_none=True``, so a number with no invoice is a
    successful call whose ``structuredContent`` is ``{}``, and the tool's
    ``outputSchema`` admits that beside a full row.
    """
    return Invoice.objects.filter(number=number)


def outstanding_total(*, currency: str) -> dict[str, Any]:
    """Backs ``invoices.outstanding``: the unsent total, in the mount's currency.

    ``currency`` is not a tool argument. It is a pool seed the server registers
    with ``pool_seeds=``, so every spec on the mount can read it, the tool's
    ``inputSchema`` does not advertise it, and a client argument of the same
    name is stripped rather than spread.
    """
    total = Invoice.objects.filter(sent=False).aggregate(total=Sum("amount_cents"))["total"]
    return {"amount_cents": total or 0, "currency": currency}
