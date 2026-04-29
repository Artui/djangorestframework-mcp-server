"""Schema generation from django-filter ``FilterSet`` declarations.

These tests run with ``django-filter`` installed (it's in the dev group).
The no-extra fallback is exercised by monkey-patching the cached module
binding, mirroring how the SSE-broker tests cover the optional-dep path.
"""

from __future__ import annotations

from typing import Any

import django_filters
import pytest

from rest_framework_mcp.schema.filterset_schema import filterset_to_schema_properties

# ---------- Per-filter-class type mapping ----------


def test_char_filter_maps_to_string() -> None:
    class FS(django_filters.FilterSet):
        name = django_filters.CharFilter()

    assert filterset_to_schema_properties(FS) == {"name": {"type": "string"}}


def test_number_filter_maps_to_number() -> None:
    class FS(django_filters.FilterSet):
        amount = django_filters.NumberFilter()

    assert filterset_to_schema_properties(FS) == {"amount": {"type": "number"}}


def test_boolean_filter_maps_to_boolean() -> None:
    class FS(django_filters.FilterSet):
        sent = django_filters.BooleanFilter()

    assert filterset_to_schema_properties(FS) == {"sent": {"type": "boolean"}}


def test_date_filter_maps_to_string_date() -> None:
    class FS(django_filters.FilterSet):
        created_at = django_filters.DateFilter()

    assert filterset_to_schema_properties(FS) == {
        "created_at": {"type": "string", "format": "date"}
    }


def test_datetime_filter_maps_to_string_date_time() -> None:
    class FS(django_filters.FilterSet):
        created_at = django_filters.DateTimeFilter()

    assert filterset_to_schema_properties(FS) == {
        "created_at": {"type": "string", "format": "date-time"}
    }


def test_time_filter_maps_to_string_time() -> None:
    class FS(django_filters.FilterSet):
        at = django_filters.TimeFilter()

    assert filterset_to_schema_properties(FS) == {"at": {"type": "string", "format": "time"}}


def test_uuid_filter_maps_to_string_uuid() -> None:
    class FS(django_filters.FilterSet):
        ref = django_filters.UUIDFilter()

    assert filterset_to_schema_properties(FS) == {"ref": {"type": "string", "format": "uuid"}}


def test_choice_filter_maps_to_enum() -> None:
    class FS(django_filters.FilterSet):
        status = django_filters.ChoiceFilter(choices=[("open", "Open"), ("closed", "Closed")])

    assert filterset_to_schema_properties(FS) == {"status": {"enum": ["open", "closed"]}}


def test_choice_filter_with_no_choices_falls_back_to_string() -> None:
    """Some custom subclasses defer choice resolution; fall back to ``string``."""

    class FS(django_filters.FilterSet):
        status = django_filters.ChoiceFilter(choices=[])

    assert filterset_to_schema_properties(FS) == {"status": {"type": "string"}}


def test_multiple_choice_filter_maps_to_array_of_enum() -> None:
    class FS(django_filters.FilterSet):
        tags = django_filters.MultipleChoiceFilter(choices=[("a", "A"), ("b", "B"), ("c", "C")])

    assert filterset_to_schema_properties(FS) == {
        "tags": {"type": "array", "items": {"enum": ["a", "b", "c"]}}
    }


def test_csv_in_filter_maps_to_array_of_scalar() -> None:
    """``BaseInFilter`` subclasses (CharIn, NumberIn, ...) → array of the scalar."""

    class NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter):
        pass

    class FS(django_filters.FilterSet):
        ids = NumberInFilter()

    assert filterset_to_schema_properties(FS) == {
        "ids": {"type": "array", "items": {"type": "number"}}
    }


def test_range_filter_maps_to_object_with_min_max() -> None:
    """``BaseRangeFilter`` subclasses → ``{min, max}`` object."""

    class NumberRangeFilter(django_filters.BaseRangeFilter, django_filters.NumberFilter):
        pass

    class FS(django_filters.FilterSet):
        amount = NumberRangeFilter()

    assert filterset_to_schema_properties(FS) == {
        "amount": {
            "type": "object",
            "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
        }
    }


def test_unknown_filter_class_falls_back_to_any() -> None:
    """Custom filters we don't recognise should not break tool discovery."""

    class WeirdFilter(django_filters.Filter):
        pass

    class FS(django_filters.FilterSet):
        thing = WeirdFilter()

    assert filterset_to_schema_properties(FS) == {"thing": {}}


def test_meta_declared_fields_are_picked_up() -> None:
    """Filters auto-generated from a ``Meta.fields`` mapping work."""
    from tests.testapp.models import Invoice

    class FS(django_filters.FilterSet):
        class Meta:
            model = Invoice
            fields = {"sent": ["exact"]}

    properties = filterset_to_schema_properties(FS)
    assert "sent" in properties
    assert properties["sent"] == {"type": "boolean"}


# ---------- Optional-extra fallback ----------


def test_import_error_when_django_filter_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Surface a clear ImportError when the [filter] extra isn't installed."""
    import rest_framework_mcp.schema.filterset_schema as mod

    monkeypatch.setattr(mod, "_django_filters", None)

    class _FS:
        pass

    with pytest.raises(ImportError, match=r"djangorestframework-mcp-server\[filter\]"):
        filterset_to_schema_properties(_FS)


# ---------- Multi-filter aggregation ----------


def test_model_choice_filter_maps_to_string() -> None:
    """``ModelChoiceFilter`` is FK-shaped — surface as string (PK)."""
    from tests.testapp.models import Invoice

    class FS(django_filters.FilterSet):
        invoice = django_filters.ModelChoiceFilter(queryset=Invoice.objects.all())

    assert filterset_to_schema_properties(FS) == {"invoice": {"type": "string"}}


def test_choice_filter_with_non_tuple_choices_uses_value_directly() -> None:
    """Some legacy code paths hand a flat list of choice values, not pairs."""

    class FS(django_filters.FilterSet):
        # Not a tuple: each entry IS the value.
        status = django_filters.ChoiceFilter(choices=["open", "closed"])

    assert filterset_to_schema_properties(FS) == {"status": {"enum": ["open", "closed"]}}


def test_multiple_filters_aggregate_into_property_dict() -> None:
    class FS(django_filters.FilterSet):
        name = django_filters.CharFilter()
        amount = django_filters.NumberFilter()
        sent = django_filters.BooleanFilter()

    properties: dict[str, Any] = filterset_to_schema_properties(FS)
    assert set(properties.keys()) == {"name", "amount", "sent"}
