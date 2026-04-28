from __future__ import annotations

import dataclasses
from typing import Any

from rest_framework import serializers

from rest_framework_mcp.schema.utils import dataclass_to_schema, serializer_to_schema


def build_input_schema(input_serializer: type | None) -> dict[str, Any]:
    """Build a JSON Schema for a tool's input.

    Accepts:
      - a DRF ``Serializer`` subclass,
      - a bare ``@dataclass`` type (the convention used by
        ``djangorestframework-services`` services),
      - ``None`` (the tool takes no input).

    Always returns a JSON Schema *object*; an empty object is the convention
    for "no parameters" so MCP clients still render the tool.
    """
    if input_serializer is None:
        return {"type": "object"}
    if isinstance(input_serializer, type) and issubclass(input_serializer, serializers.Serializer):
        return serializer_to_schema(input_serializer())
    if isinstance(input_serializer, type) and dataclasses.is_dataclass(input_serializer):
        return dataclass_to_schema(input_serializer)
    return {"type": "object"}


__all__ = ["build_input_schema"]
