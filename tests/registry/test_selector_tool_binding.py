from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding


def _sel() -> list[dict[str, str]]:
    return []


def test_rejects_schema_on_with_content_off_at_construction() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        SelectorToolBinding(
            name="bad",
            description=None,
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=_sel),
            include_output_schema=True,
            include_structured_content=False,
        )
    assert "bad" in str(excinfo.value)


def test_allows_schema_off_with_content_on() -> None:
    binding = SelectorToolBinding(
        name="t",
        description=None,
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_sel),
        include_output_schema=False,
        include_structured_content=True,
    )
    assert binding.include_output_schema is False
    assert binding.include_structured_content is True


def test_retrieve_kind_rejects_filter_set() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        SelectorToolBinding(
            name="r",
            description=None,
            # filter_set lives on the spec now; any truthy value triggers the guard.
            spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel, filter_set=object),
        )
    assert "spec.kind=RETRIEVE" in str(excinfo.value)
    assert "filter_set" in str(excinfo.value)


def test_retrieve_kind_rejects_ordering_fields() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        SelectorToolBinding(
            name="r",
            description=None,
            spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel),
            ordering_fields=("created_at",),
        )
    assert "ordering_fields" in str(excinfo.value)


def test_retrieve_kind_rejects_paginate() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        SelectorToolBinding(
            name="r",
            description=None,
            spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel),
            paginate=True,
        )
    assert "paginate" in str(excinfo.value)


def test_retrieve_kind_lists_every_offending_knob() -> None:
    """When multiple list-only knobs are set together, the error names all of them."""
    with pytest.raises(ImproperlyConfigured) as excinfo:
        SelectorToolBinding(
            name="r",
            description=None,
            spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel, filter_set=object),
            ordering_fields=("x",),
            paginate=True,
        )
    msg = str(excinfo.value)
    assert "filter_set" in msg and "ordering_fields" in msg and "paginate" in msg


def test_retrieve_kind_without_list_knobs_constructs_cleanly() -> None:
    binding = SelectorToolBinding(
        name="r",
        description=None,
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_sel),
    )
    assert binding.kind is SelectorKind.RETRIEVE
