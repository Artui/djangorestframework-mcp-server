from __future__ import annotations

from typing import Any

from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.pagination import paginate
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode
from rest_framework_mcp.protocol.resource_template import ResourceTemplate


def handle_resources_templates_list(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """List the parameterised resource templates the server exposes, paginated."""
    cursor: Any = (params or {}).get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'cursor' must be a string")
    try:
        page, next_cursor = paginate(context.resources.templates(), cursor)
    except ValueError as exc:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc))

    templates: list[dict[str, Any]] = []
    for binding in page:
        tpl = ResourceTemplate(
            uri_template=binding.uri_template,
            name=binding.name,
            description=binding.description,
            title=binding.title,
            mime_type=binding.mime_type,
            annotations=dict(binding.annotations) or None,
        )
        templates.append(tpl.to_dict())
    response: dict[str, Any] = {"resourceTemplates": templates}
    if next_cursor is not None:
        response["nextCursor"] = next_cursor
    return response


__all__ = ["handle_resources_templates_list"]
