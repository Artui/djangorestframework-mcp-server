from __future__ import annotations

import dataclasses
from typing import Any

from rest_framework import serializers

from rest_framework_mcp.schema.utils import dataclass_to_schema, serializer_to_schema


def build_input_schema(input_serializer: type | None, *, partial: bool = False) -> dict[str, Any]:
    """Build a JSON Schema for a tool's input.

    Accepts:
      - a DRF ``Serializer`` subclass,
      - a bare ``@dataclass`` type (the convention used by
        ``djangorestframework-services`` services),
      - ``None`` (the tool takes no input).

    Always returns a JSON Schema *object*; an empty object is the convention
    for "no parameters" so MCP clients still render the tool.

    ``partial=True`` (sister-repo 0.16's ``spec.partial`` forced on) drops
    the ``required`` list — the validator accepts omitted fields, so
    advertising them as required would make schema-strict clients reject
    calls the server happily validates.
    """
    schema: dict[str, Any]
    if input_serializer is None:
        schema = {"type": "object"}
    elif isinstance(input_serializer, type) and issubclass(
        input_serializer, serializers.Serializer
    ):
        schema = serializer_to_schema(input_serializer())
    elif isinstance(input_serializer, type) and dataclasses.is_dataclass(input_serializer):
        schema = dataclass_to_schema(input_serializer)
    else:
        schema = {"type": "object"}
    if partial:
        schema.pop("required", None)
    return schema


__all__ = ["build_input_schema"]
