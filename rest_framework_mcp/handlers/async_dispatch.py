from __future__ import annotations

from typing import Any

from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.dispatch import dispatch
from rest_framework_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError


async def adispatch(
    method: str,
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> Any | JsonRpcError:
    """Async sibling of :func:`dispatch`.

    Only the I/O-bound handlers (``tools/call``, ``resources/read``,
    ``prompts/get``) have async-native variants. The rest are CPU-only —
    schema generation, capability advertisement, error wrapping — and run
    inline through the sync :func:`dispatch` table without blocking the
    event loop noticeably.
    """
    if method == "tools/call":
        return await handle_tools_call_async(params, context)
    if method == "resources/read":
        return await handle_resources_read_async(params, context)
    if method == "prompts/get":
        return await handle_prompts_get_async(params, context)
    return dispatch(method, params, context)


__all__ = ["adispatch"]
