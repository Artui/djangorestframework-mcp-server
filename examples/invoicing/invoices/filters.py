"""``django-filter`` declarations driving ``invoices.list``."""

from __future__ import annotations

import django_filters

from invoices.models import Invoice


class InvoiceFilterSet(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()
    min_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="gte")
    max_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="lte")
    created_after = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    number = django_filters.CharFilter(lookup_expr="icontains")
    # Ordering is a filter like any other. ``OrderingFilter`` subclasses
    # ``ChoiceFilter``, so it reflects into the tool's ``inputSchema`` as a
    # labelled choice over the public names on the right — never the ORM paths
    # on the left — and one declaration serves the HTTP transport and MCP.
    ordering = django_filters.OrderingFilter(
        fields=(("created_at", "created"), ("amount_cents", "amount")),
    )

    class Meta:
        model = Invoice
        fields = ["sent", "min_amount", "max_amount", "created_after", "number"]
