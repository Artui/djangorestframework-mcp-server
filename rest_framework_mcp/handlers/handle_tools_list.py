from __future__ import annotations

from typing import Any

from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.pagination import paginate
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode
from rest_framework_mcp.protocol.tool import Tool
from rest_framework_mcp.schema.input_schema import build_input_schema
from rest_framework_mcp.schema.output_schema import build_output_schema


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
    try:
        page, next_cursor = paginate(context.tools.all(), cursor)
    except ValueError as exc:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc))

    tools: list[dict[str, Any]] = []
    for binding in page:
        tool = Tool(
            name=binding.name,
            description=binding.description,
            title=binding.title,
            input_schema=build_input_schema(binding.spec.input_serializer),
            output_schema=build_output_schema(binding.spec.output_serializer),
            annotations=dict(binding.annotations) or None,
        )
        tools.append(tool.to_dict())
    response: dict[str, Any] = {"tools": tools}
    if next_cursor is not None:
        response["nextCursor"] = next_cursor
    return response


__all__ = ["handle_tools_list"]
