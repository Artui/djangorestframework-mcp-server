from __future__ import annotations

from typing import Any

from rest_framework_services import spec_to_json_schema

from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.schema.input_schema import build_input_schema


def build_service_tool_input_schema(binding: ToolBinding) -> dict[str, Any]:
    """Build the JSON Schema for a service tool's ``inputSchema``.

    The shape is ``spec.input_serializer`` verbatim (``spec.partial is True`` drops
    ``required``, mirroring the dispatch-time partial-validation contract), plus any
    registered [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg] properties
    merged in.

    A ``UrlKwarg(required=True)`` joins ``required`` and ``spec.partial`` does
    **not** relax it: partial validation is about the *payload* the serializer
    checks, and a URL kwarg is routed to the off-HTTP ``view.kwargs`` at
    dispatch rather than into that payload.

    A ``many=True`` spec validates a list, and ``arguments`` is always an object,
    so the list travels under the one argument ``spec.many_argument`` names. That
    shape is drf-services' ``spec_to_json_schema`` taken whole rather than wrapped
    around ``build_input_schema`` here: the ``minItems`` / ``maxItems`` a list
    serializer declares in ``many_init`` are only visible to the reflection that
    builds that list serializer, and a wrapper assembled here would advertise an
    unbounded list. The item inside is the same ``serializer_to_json_schema`` call
    either way. The reflection also merges ``metadata["json_schema"]["input"]``
    on top, which the single-item shape above does not read.
    """
    spec = binding.spec
    schema: dict[str, Any] = (
        # ``phase="input"`` never answers ``None`` (only the output phase is
        # nullable), so ``or {}`` narrows the type and never substitutes.
        (spec_to_json_schema(spec, phase="input") or {})
        if spec.many
        else build_input_schema(spec.input_serializer, partial=spec.partial is True)
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
