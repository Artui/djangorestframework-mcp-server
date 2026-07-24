from __future__ import annotations

from typing import Any

from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.schema.input_schema import build_input_schema


def build_service_tool_input_schema(binding: ToolBinding) -> dict[str, Any]:
    """Build the JSON Schema for a service tool's ``inputSchema``.

    The service tool's shape is its ``spec.input_serializer`` verbatim
    (``spec.partial is True`` drops ``required``, mirroring the dispatch-time
    partial-validation contract), plus any registered :class:`UrlKwarg`
    properties merged in. URL kwargs are model-supplied but routed to the
    off-HTTP ``view.kwargs`` at dispatch (never the service's validated
    payload), so they are advertised here as optional properties and popped
    before the serializer sees the arguments.
    """
    schema: dict[str, Any] = build_input_schema(
        binding.spec.input_serializer, partial=binding.spec.partial is True
    )
    if not binding.url_kwargs:
        return schema
    properties: dict[str, Any] = dict(schema.get("properties", {}))
    for url_kwarg in binding.url_kwargs:
        properties[url_kwarg.name] = url_kwarg.json_schema()
    return {**schema, "type": "object", "properties": properties}


__all__ = ["build_service_tool_input_schema"]
