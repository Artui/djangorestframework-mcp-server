from __future__ import annotations

import dataclasses
from typing import Any

from rest_framework import serializers

from rest_framework_mcp.schema.utils import dataclass_to_schema, serializer_to_schema


def build_output_schema(output_serializer: type | None) -> dict[str, Any] | None:
    """Build a JSON Schema for a tool's output, or ``None`` if not declared.

    MCP makes ``outputSchema`` optional; we only emit it when the service has
    an explicit ``output_serializer`` so we don't fabricate misleading shapes.
    """
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
