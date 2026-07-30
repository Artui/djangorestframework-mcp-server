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
    payload), so they are popped before the serializer sees the arguments.

    A ``UrlKwarg(required=True)`` joins the schema's ``required`` list — a route
    capture the spec cannot run without, so the model is told up front rather
    than discovering it through a failed call. ``spec.partial`` does **not**
    relax it: partial validation is about the *payload* the serializer checks,
    and a URL kwarg is not part of that payload.
    """
    schema: dict[str, Any] = build_input_schema(
        binding.spec.input_serializer, partial=binding.spec.partial is True
    )
    if not binding.url_kwargs and not binding.query_params:
        return schema
    properties: dict[str, Any] = dict(schema.get("properties", {}))
    required: list[str] = list(schema.get("required", []))
    for url_kwarg in binding.url_kwargs:
        properties[url_kwarg.name] = url_kwarg.json_schema()
        if url_kwarg.required and url_kwarg.name not in required:
            required.append(url_kwarg.name)
    # Query params never join ``required``: a read-shaping param the spec runs
    # fine without cannot be required, which is why ``QueryParam`` carries no
    # such flag in the first place.
    for query_param in binding.query_params:
        properties[query_param.name] = query_param.json_schema()
    merged: dict[str, Any] = {**schema, "type": "object", "properties": properties}
    if required:
        merged["required"] = required
    return merged


__all__ = ["build_service_tool_input_schema"]
