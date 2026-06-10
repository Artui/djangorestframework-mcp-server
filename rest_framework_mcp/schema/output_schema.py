from __future__ import annotations

import dataclasses
from typing import Any

from rest_framework import serializers
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.schema.utils import dataclass_to_schema, serializer_to_schema


def build_output_schema(
    output_serializer: type | None,
    *,
    kind: SelectorKind | None = None,
    paginate: bool = False,
) -> dict[str, Any] | None:
    """Build a JSON Schema for a tool's output, or ``None`` if not declared.

    MCP makes ``outputSchema`` optional; we only emit it when the service has
    an explicit ``output_serializer`` so we don't fabricate misleading shapes.

    ``kind`` / ``paginate`` make the schema match what the dispatch pipeline
    actually returns (clients SHOULD validate ``structuredContent`` against
    ``outputSchema``, so a single-item schema on a LIST tool makes strict
    clients reject every result):

    - ``kind=None`` / ``RETRIEVE`` — the bare item schema (service tools,
      single-instance reads).
    - ``kind=LIST, paginate=False`` — ``{type: array, items: <item>}``.
      Note the payload is a bare array; for a fully spec-compliant
      *object*-shaped ``structuredContent``, enable ``paginate=True``.
    - ``kind=LIST, paginate=True`` — the pagination envelope
      ``{items, page, totalPages, hasNext}``.
    """
    item_schema: dict[str, Any] | None = _item_schema(output_serializer)
    if item_schema is None:
        return None
    if kind is not SelectorKind.LIST:
        return item_schema
    array_schema: dict[str, Any] = {"type": "array", "items": item_schema}
    if not paginate:
        return array_schema
    return {
        "type": "object",
        "properties": {
            "items": array_schema,
            "page": {"type": "integer"},
            "totalPages": {"type": "integer"},
            "hasNext": {"type": "boolean"},
        },
        "required": ["items", "page", "totalPages", "hasNext"],
    }


def _item_schema(output_serializer: type | None) -> dict[str, Any] | None:
    if output_serializer is None:
        return None
    if isinstance(output_serializer, type) and issubclass(
        output_serializer, serializers.Serializer
    ):
        return serializer_to_schema(output_serializer())
    if isinstance(output_serializer, type) and dataclasses.is_dataclass(output_serializer):
        return dataclass_to_schema(output_serializer)
    return None


__all__ = ["build_output_schema"]
