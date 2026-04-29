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

    class Meta:
        model = Invoice
        fields = ["sent", "min_amount", "max_amount", "created_after", "number"]
