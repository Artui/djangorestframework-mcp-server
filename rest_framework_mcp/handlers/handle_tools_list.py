from __future__ import annotations

from typing import Any

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import JsonRpcErrorCode, UnknownArguments
from rest_framework_mcp.handlers.is_binding_listable import is_binding_listable
from rest_framework_mcp.handlers.pagination import paginate
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.tool import Tool
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.schema.input_schema import build_input_schema
from rest_framework_mcp.schema.output_schema import build_output_schema
from rest_framework_mcp.schema.selector_tool_schema import build_selector_tool_input_schema


def handle_tools_list(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Return the catalog of tools the server exposes, paginated.

    JSON Schemas are rebuilt on every request rather than cached on the
    binding — discovery already runs at router-construction time, so the
    relative cost is small and it keeps bindings cheap to construct.

    Pagination is opaque-cursor per the MCP spec: clients pass back the
    ``nextCursor`` they received without inspecting it.
    """
    cursor: Any = (params or {}).get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'cursor' must be a string")

    # Per-caller visibility filter (Phase 10g): when enabled, drop bindings
    # the current token can't invoke before paginating, so ``nextCursor``
    # reflects the visible slice rather than the full registry.
    bindings = list(context.tools.all())
    if get_setting("FILTER_LISTINGS_BY_PERMISSIONS"):
        bindings = [
            b for b in bindings if is_binding_listable(b, context.http_request, context.token)
        ]

    try:
        page, next_cursor = paginate(bindings, cursor)
    except ValueError as exc:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc))

    tools: list[dict[str, Any]] = []
    for binding in page:
        # Selector tools merge filter / ordering / pagination args into
        # their inputSchema; service tools just expose the input
        # serializer's schema verbatim.
        if isinstance(binding, SelectorToolBinding):
            input_schema = build_selector_tool_input_schema(binding)
        else:
            input_schema = build_input_schema(binding.spec.input_serializer)
        # Stamp ``additionalProperties`` per the binding's unknown-argument
        # policy. ``REJECT`` declares the schema as closed; ``PASSTHROUGH``
        # and ``IGNORE`` keep it open. ``build_input_schema`` and
        # ``build_selector_tool_input_schema`` always return a
        # ``"type": "object"`` shape, so this stamps every emitted schema.
        input_schema = dict(input_schema)
        input_schema["additionalProperties"] = (
            binding.unknown_arguments is not UnknownArguments.REJECT
        )
        # ``outputSchema`` and ``structuredContent`` are independently
        # toggleable, but the spec forbids one combination — advertising
        # the schema while suppressing the content. ``resolve_structured_output``
        # validates the asymmetric rule and raises ``ImproperlyConfigured``
        # for the bad combo before we serialize anything.
        emit_output_schema, _emit_structured_content = resolve_structured_output(
            include_output_schema_override=binding.include_output_schema,
            include_structured_content_override=binding.include_structured_content,
            binding_name=binding.name,
        )
        tool = Tool(
            name=binding.name,
            description=binding.description,
            title=binding.title,
            input_schema=input_schema,
            output_schema=(
                build_output_schema(binding.spec.output_serializer) if emit_output_schema else None
            ),
            annotations=dict(binding.annotations) or None,
        )
        tools.append(tool.to_dict())
    response: dict[str, Any] = {"tools": tools}
    if next_cursor is not None:
        response["nextCursor"] = next_cursor
    return response


__all__ = ["handle_tools_list"]
