from __future__ import annotations

from typing import Any

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.is_binding_listable import is_binding_listable
from rest_framework_mcp.handlers.pagination import paginate
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.resource import Resource


def handle_resources_list(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """List concrete resources (those without template variables), paginated.

    Resource templates are exposed via ``resources/templates/list`` instead.
    """
    cursor: Any = (params or {}).get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'cursor' must be a string")

    bindings = list(context.resources.concrete())
    if get_setting("FILTER_LISTINGS_BY_PERMISSIONS"):
        bindings = [
            b for b in bindings if is_binding_listable(b, context.http_request, context.token)
        ]

    try:
        page, next_cursor = paginate(bindings, cursor)
    except ValueError as exc:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc))

    items: list[dict[str, Any]] = []
    for binding in page:
        resource = Resource(
            uri=binding.uri_template,
            name=binding.name,
            description=binding.description,
            title=binding.title,
            mime_type=binding.mime_type,
            annotations=dict(binding.annotations) or None,
        )
        items.append(resource.to_dict())
    response: dict[str, Any] = {"resources": items}
    if next_cursor is not None:
        response["nextCursor"] = next_cursor
    return response


__all__ = ["handle_resources_list"]
