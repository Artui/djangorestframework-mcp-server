from __future__ import annotations

from typing import Any

from rest_framework_mcp.registry.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.schema.filterset_schema import filterset_to_schema_properties
from rest_framework_mcp.schema.input_schema import build_input_schema


def build_selector_tool_input_schema(binding: SelectorToolBinding) -> dict[str, Any]:
    """Build the JSON Schema for a selector tool's ``inputSchema``.

    Merges four sources, in order of precedence (later sources override
    earlier ones on key collision):

    1. **``spec.input_serializer``** — any explicit input shape declared
       by the consumer (e.g. for tool-specific args that aren't filter
       params). All required-marked fields stay required.
    2. **``filter_set``** — properties derived from
       ``django-filter`` filter declarations. All optional.
    3. **``ordering_fields``** — adds an ``ordering`` property as an enum
       of ``"<field>"`` and ``"-<field>"`` values. Optional.
    4. **``paginate=True``** — adds optional ``page`` (positive integer)
       and ``limit`` (positive integer) properties.

    The final schema is always an object with ``"type": "object"``,
    ``"properties": {...}``, and ``"required": [...]`` only when at
    least one required field exists.
    """
    base: dict[str, Any] = build_input_schema(binding.input_serializer)
    properties: dict[str, Any] = dict(base.get("properties", {}))
    required: list[str] = list(base.get("required", []))

    if binding.filter_set is not None:
        for name, schema in filterset_to_schema_properties(binding.filter_set).items():
            properties[name] = schema  # filter args are always optional

    if binding.ordering_fields:
        ordering_values: list[str] = []
        for field in binding.ordering_fields:
            ordering_values.append(field)
            ordering_values.append(f"-{field}")
        properties["ordering"] = {"enum": ordering_values}

    if binding.paginate:
        properties["page"] = {"type": "integer", "minimum": 1}
        properties["limit"] = {"type": "integer", "minimum": 1}

    out: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


__all__ = ["build_selector_tool_input_schema"]
