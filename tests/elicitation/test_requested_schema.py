"""Turning a service's ``schema={...}`` into a form a client will render.

The subset MCP allows here is narrow — *"only top-level properties, without
nesting"* — and a client is entitled to reject anything else outright. So the
job is half translation and half refusing to ship something that would fail a
long way from the service that caused it.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.elicitation.build_requested_schema import build_requested_schema


def test_the_properties_are_wrapped_in_the_objects_the_spec_expects() -> None:
    assert build_requested_schema({"confirmed": {"type": "boolean"}}) == {
        "type": "object",
        "properties": {"confirmed": {"type": "boolean"}},
        "required": ["confirmed"],
    }


def test_a_property_with_a_default_is_not_required() -> None:
    """The only signal available, and the same one the dict would carry as a
    serializer field: a default says what to do without an answer."""
    built = build_requested_schema(
        {"confirmed": {"type": "boolean"}, "reason": {"type": "string", "default": ""}}
    )
    assert built["required"] == ["confirmed"]


def test_required_is_omitted_rather_than_sent_empty() -> None:
    built = build_requested_schema({"reason": {"type": "string", "default": ""}})
    assert "required" not in built


def test_declaration_order_is_kept() -> None:
    built = build_requested_schema(
        {"b": {"type": "string"}, "a": {"type": "string"}, "c": {"type": "string"}}
    )
    assert built["required"] == ["b", "a", "c"]
    assert list(built["properties"]) == ["b", "a", "c"]


@pytest.mark.parametrize(
    "definition",
    [
        {"type": "string", "format": "email"},
        {"type": "number", "minimum": 0},
        {"type": "integer"},
        {"type": "boolean"},
        {"type": "string", "enum": ["red", "green"]},
        {"type": "string", "oneOf": [{"const": "r", "title": "Red"}]},
        {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}},
        {"type": "array", "items": {"anyOf": [{"const": "a", "title": "A"}]}},
    ],
)
def test_every_shape_the_spec_allows_passes_through(definition: dict[str, Any]) -> None:
    assert build_requested_schema({"field": definition})["properties"]["field"] == definition


@pytest.mark.parametrize(
    "definition",
    [
        {"type": "object", "properties": {"name": {"type": "string"}}},
        {"type": "null"},
        {"type": "array"},
        {"type": "array", "items": {"type": "string"}},
        {"description": "no type at all"},
        {"type": ["string", "null"]},
    ],
)
def test_a_shape_no_form_can_hold_is_refused(definition: dict[str, Any]) -> None:
    with pytest.raises(ImproperlyConfigured, match="cannot render"):
        build_requested_schema({"profile": definition})


def test_a_value_that_is_not_a_schema_at_all_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="instead of a schema object"):
        build_requested_schema({"confirmed": "boolean"})


def test_the_message_names_the_offending_property() -> None:
    """The author has one dict and several fields; which one is wrong is the
    entire content of the error."""
    with pytest.raises(ImproperlyConfigured, match="'profile'"):
        build_requested_schema({"confirmed": {"type": "boolean"}, "profile": {"type": "object"}})
