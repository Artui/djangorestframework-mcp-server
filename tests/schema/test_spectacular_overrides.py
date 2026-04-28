"""drf-spectacular integration: layer ``@extend_schema_*`` metadata onto JSON Schema.

These tests exercise the production path with ``drf-spectacular`` installed
(it's in the dev group). Coverage of the *no-spectacular* path lives in
``test_no_spectacular_branch_is_noop`` below — we monkey-patch the
annotation lookup so the helper takes the no-op branch even though the
package is importable.
"""

from __future__ import annotations

from typing import Any

import pytest
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema_field,
    extend_schema_serializer,
)
from rest_framework import serializers

from rest_framework_mcp.schema.input_schema import build_input_schema
from rest_framework_mcp.schema.spectacular_overrides import (
    apply_field_override,
    apply_serializer_overrides,
)

# ---------- @extend_schema_serializer (class-level) ----------


def test_exclude_fields_drops_property_and_required_entry() -> None:
    @extend_schema_serializer(exclude_fields=["secret"])
    class Ser(serializers.Serializer):
        name = serializers.CharField()
        secret = serializers.CharField()  # required by default

    schema = build_input_schema(Ser)
    assert "secret" not in schema["properties"]
    assert "secret" not in schema.get("required", [])
    assert "name" in schema["properties"]


def test_exclude_fields_strips_required_entirely_when_only_required_field_excluded() -> None:
    @extend_schema_serializer(exclude_fields=["only_one"])
    class Ser(serializers.Serializer):
        only_one = serializers.CharField()  # the only required field
        optional = serializers.CharField(required=False)

    schema = build_input_schema(Ser)
    # ``required`` was [only_one] and got drained → key is gone, not empty.
    assert "required" not in schema


def test_deprecate_fields_marks_property_deprecated() -> None:
    @extend_schema_serializer(deprecate_fields=["legacy_id"])
    class Ser(serializers.Serializer):
        legacy_id = serializers.CharField(required=False)
        modern_id = serializers.CharField()

    schema = build_input_schema(Ser)
    assert schema["properties"]["legacy_id"]["deprecated"] is True
    # Non-deprecated fields untouched.
    assert "deprecated" not in schema["properties"]["modern_id"]


def test_deprecate_fields_skips_unknown_names() -> None:
    """Naming a non-existent field shouldn't crash — silently ignored."""

    @extend_schema_serializer(deprecate_fields=["does_not_exist"])
    class Ser(serializers.Serializer):
        name = serializers.CharField()

    schema = build_input_schema(Ser)
    assert "does_not_exist" not in schema["properties"]


def test_examples_aggregate_into_json_schema_examples() -> None:
    @extend_schema_serializer(
        examples=[
            OpenApiExample("First", value={"name": "alice"}),
            OpenApiExample("Second", value={"name": "bob"}),
        ]
    )
    class Ser(serializers.Serializer):
        name = serializers.CharField()

    schema = build_input_schema(Ser)
    assert schema["examples"] == [{"name": "alice"}, {"name": "bob"}]


def test_examples_with_none_value_are_filtered() -> None:
    """Placeholder examples (no ``value=``) shouldn't pollute the schema."""

    @extend_schema_serializer(
        examples=[
            OpenApiExample("WithValue", value={"name": "x"}),
            OpenApiExample("Placeholder"),  # value=None
        ]
    )
    class Ser(serializers.Serializer):
        name = serializers.CharField()

    schema = build_input_schema(Ser)
    assert schema["examples"] == [{"name": "x"}]


def test_no_examples_means_no_examples_key() -> None:
    """Schemas stay lean when no examples are declared."""

    @extend_schema_serializer(exclude_fields=["x"])
    class Ser(serializers.Serializer):
        name = serializers.CharField()
        x = serializers.CharField(required=False)

    schema = build_input_schema(Ser)
    assert "examples" not in schema


def test_serializer_without_spectacular_decorator_is_unchanged() -> None:
    class Ser(serializers.Serializer):
        name = serializers.CharField()

    schema = build_input_schema(Ser)
    # Plain old DRF schema, no ``examples`` / ``deprecated`` markers.
    assert schema == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }


# ---------- @extend_schema_field (field-level) ----------


def test_field_class_override_replaces_default_schema() -> None:
    @extend_schema_field({"type": "string", "format": "iban"})
    class IBANField(serializers.CharField):
        pass

    class Ser(serializers.Serializer):
        account = IBANField()

    schema = build_input_schema(Ser)
    assert schema["properties"]["account"] == {"type": "string", "format": "iban"}


def test_field_override_does_not_pollute_class_dict() -> None:
    """Mutating the returned schema must not leak into the spectacular annotation."""

    @extend_schema_field({"type": "string", "format": "iban"})
    class IBANField(serializers.CharField):
        pass

    class Ser(serializers.Serializer):
        account = IBANField()

    schema_a = build_input_schema(Ser)
    schema_a["properties"]["account"]["description"] = "added later"

    schema_b = build_input_schema(Ser)
    assert "description" not in schema_b["properties"]["account"]


def test_field_override_with_non_dict_falls_through() -> None:
    """Non-dict ``field`` values (OpenApiTypes enum, serializer class) keep the default."""

    class CustomField(serializers.CharField):
        # Simulate the shape spectacular stores when you pass a serializer
        # class or an enum — not a plain dict.
        _spectacular_annotation = {"field": object(), "field_component_name": None}

    class Ser(serializers.Serializer):
        x = CustomField()

    schema = build_input_schema(Ser)
    # Falls back to the default DRF-derived schema for CharField.
    assert schema["properties"]["x"] == {"type": "string"}


# ---------- direct helper coverage ----------


def test_apply_serializer_overrides_noop_without_annotation() -> None:
    class Ser(serializers.Serializer):
        name = serializers.CharField()

    initial = {"type": "object", "properties": {"name": {"type": "string"}}}
    result = apply_serializer_overrides(initial, Ser)
    assert result is initial  # mutated in place, identity preserved
    assert result == {"type": "object", "properties": {"name": {"type": "string"}}}


def test_apply_serializer_overrides_skips_field_level_annotation() -> None:
    """A ``_spectacular_annotation`` from ``@extend_schema_field`` shouldn't trigger
    serializer-level processing — it carries different keys and a different intent.
    """

    @extend_schema_field({"type": "string", "format": "iban"})
    class IBAN(serializers.CharField):
        pass

    initial = {"type": "object", "properties": {}}
    result = apply_serializer_overrides(initial, IBAN)
    assert result == {"type": "object", "properties": {}}


def test_apply_field_override_returns_default_when_annotation_absent() -> None:
    """Bare DRF fields don't carry ``_spectacular_annotation``."""
    field = serializers.CharField()
    default = {"type": "string"}
    result = apply_field_override(field, default)
    assert result is default


def test_no_spectacular_branch_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a class genuinely has no annotation attribute, the helper returns early.

    Mirrors the behaviour consumers see when ``drf-spectacular`` isn't
    installed — annotated and non-annotated classes alike pass through
    untouched.
    """

    class Ser(serializers.Serializer):
        name = serializers.CharField()

    # Defensive: even with the module imported, removing the attribute
    # should be fully tolerated.
    monkeypatch.delattr(Ser, "_spectacular_annotation", raising=False)
    schema: dict[str, Any] = {"type": "object", "properties": {"name": {"type": "string"}}}
    out = apply_serializer_overrides(schema, Ser)
    assert out is schema
    assert out == {"type": "object", "properties": {"name": {"type": "string"}}}
