from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.dispatch import dispatch
from rest_framework_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import run_with_deadline
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


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
        # Not wrapped here: a tool call resolves its own deadline from the
        # binding (falling back to the server's), so wrapping it again at this
        # level would start a second, unrelated timer around the first.
        return await handle_tools_call_async(params, context)
    if method == "resources/read":
        return await _with_deadline(handle_resources_read_async(params, context), context)
    if method == "prompts/get":
        return await _with_deadline(handle_prompts_get_async(params, context), context)
    return dispatch(method, params, context)


async def _with_deadline(coro: Awaitable[Any], context: MCPCallContext) -> Any:
    """Apply the server's dispatch deadline, rendering expiry as a JSON-RPC error.

    Resources and prompts have no per-binding override — the knob lives on tool
    bindings, where a slow report generator genuinely differs from a lookup —
    and no ``isError`` envelope to explain themselves in, so expiry is a
    protocol error rather than a result.
    """
    try:
        return await run_with_deadline(coro, context.config.dispatch_timeout)
    except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct from builtins
        return JsonRpcError(
            JsonRpcErrorCode.SERVER_ERROR,
            "The request exceeded this server's dispatch deadline and was abandoned.",
        )


__all__ = ["adispatch"]
