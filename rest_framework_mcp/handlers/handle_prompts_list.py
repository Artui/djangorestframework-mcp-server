from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.is_binding_listable import is_binding_listable
from rest_framework_mcp.handlers.pagination import paginate
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.prompt import Prompt


def handle_prompts_list(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Return the catalog of prompts the server exposes, paginated.

    Mirrors ``tools/list`` / ``resources/list`` — opaque cursor in,
    ``nextCursor`` out when the page doesn't reach the end.
    """
    cursor: Any = (params or {}).get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'cursor' must be a string")

    bindings = list(context.prompts.all())
    if context.config.filter_listings_by_permissions:
        bindings = [
            b for b in bindings if is_binding_listable(b, context.http_request, context.token)
        ]

    try:
        page, next_cursor = paginate(bindings, cursor, page_size=context.config.page_size)
    except ValueError as exc:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc))

    items: list[dict[str, Any]] = []
    for binding in page:
        prompt = Prompt(
            name=binding.name,
            description=binding.description,
            title=binding.title,
            arguments=list(binding.arguments),
            annotations=dict(binding.annotations) or None,
        )
        items.append(prompt.to_dict())
    response: dict[str, Any] = {"prompts": items}
    if next_cursor is not None:
        response["nextCursor"] = next_cursor
    return response


__all__ = ["handle_prompts_list"]
