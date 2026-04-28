from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework.fields import empty as _drf_empty


def apply_serializer_overrides(schema: dict[str, Any], serializer_class: type) -> dict[str, Any]:
    """Layer ``@extend_schema_serializer`` metadata onto a JSON Schema object.

    drf-spectacular stores the decorator's keyword arguments on the
    serializer class as a ``_spectacular_annotation`` dict with keys
    ``exclude_fields`` / ``deprecate_fields`` / ``examples`` /
    ``component_name``. We honor the first three:

    - **exclude_fields** — drop the named properties and strip them from
      ``required`` (they're not part of the public surface).
    - **deprecate_fields** — set ``"deprecated": true`` on the named
      properties; supported by JSON Schema 2020-12 and most MCP clients
      surface it.
    - **examples** — aggregate ``OpenApiExample.value`` into a JSON Schema
      ``examples`` array. ``None`` values (placeholder examples) are
      filtered out.

    ``component_name`` and ``extensions`` aren't relevant to MCP's
    ``inputSchema`` (which is inlined per-tool, not OpenAPI-componentised)
    so they're ignored.

    No-op when the class isn't decorated, so calling unconditionally from
    the main schema-build path keeps the integration cost-free for
    consumers who don't use spectacular.
    """
    annotation: Any = getattr(serializer_class, "_spectacular_annotation", None)
    if not isinstance(annotation, dict):
        return schema
    # Field-level annotations (from ``@extend_schema_field`` on a Field
    # subclass) live on the same attribute name but carry a ``field`` key.
    # Skip those — they're applied per-field via ``apply_field_override``.
    if "field" in annotation and "exclude_fields" not in annotation:
        return schema

    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    for excluded in annotation.get("exclude_fields") or ():
        properties.pop(excluded, None)
        if excluded in required:
            required.remove(excluded)
    if not required and "required" in schema:
        # Keep the schema lean — no empty ``required`` arrays.
        del schema["required"]

    for deprecated in annotation.get("deprecate_fields") or ():
        if deprecated in properties:
            properties[deprecated]["deprecated"] = True

    example_values: list[Any] = []
    for example in annotation.get("examples") or ():
        # ``OpenApiExample(value=...)`` defaults to ``rest_framework.fields.empty``
        # (a sentinel type, not ``None``) when the caller doesn't supply one.
        # Filter both shapes so placeholder examples don't pollute the schema.
        value: Any = getattr(example, "value", None)
        if value is not None and value is not _drf_empty:
            example_values.append(value)
    if example_values:
        schema["examples"] = example_values

    return schema


def apply_field_override(
    field: serializers.Field, default_schema: dict[str, Any]
) -> dict[str, Any]:
    """Replace a field's JSON Schema fragment when ``@extend_schema_field`` was applied.

    ``@extend_schema_field`` on a custom ``Field`` subclass stores
    ``{"field": <OpenAPI-schema-or-typeref>, "field_component_name": ...}``
    on the class. When ``field`` is a dict (the most common form — e.g.
    ``{"type": "string", "format": "iban"}``) we use it verbatim; the
    OpenAPI 3.0 schema dialect is JSON-Schema-compatible at the field
    level for the kinds of overrides users typically apply.

    Non-dict forms (an OpenApiTypes enum, a serializer class) fall through
    to the default schema so we don't fabricate output we can't reason
    about. Document the limitation rather than guessing.

    Returns ``default_schema`` unchanged when no annotation is present.
    """
    annotation: Any = getattr(field, "_spectacular_annotation", None)
    if not isinstance(annotation, dict):
        return default_schema
    override: Any = annotation.get("field")
    if isinstance(override, dict):
        # Copy so callers can mutate without poisoning the class-level dict.
        return dict(override)
    return default_schema


__all__ = ["apply_field_override", "apply_serializer_overrides"]
