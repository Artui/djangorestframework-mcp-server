from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from asgiref.sync import sync_to_async

from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.dispatch import dispatch
from rest_framework_mcp.handlers.handle_completion_complete import handle_completion_complete
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
    """Async sibling of ``dispatch``.

    Only the I/O-bound handlers (``tools/call``, ``resources/read``,
    ``prompts/get``) have async-native variants; ``completion/complete``
    borrows the executor instead.

    **The rest go through the thread-sensitive executor rather than running
    inline on the loop.** They were once described as CPU-only, and they are
    not: ``tasks/*`` read the task store, whose default
    (``DjangoCacheTaskStore``) is the Django cache — a ``DatabaseCache``
    configuration reaches ``django.db`` and Django raises
    ``SynchronousOnlyOperation`` straight off the loop — and the four list
    handlers evaluate binding permissions, which is where a consumer's
    ``DjangoPermRequired`` runs a query. The thread hop costs a handful of
    microseconds on the handlers that genuinely need nothing; a task the client
    can create but never collect is not recoverable at all.
    """
    if method == "tools/call":
        # Not wrapped: a tool call resolves its own deadline from the binding,
        # so a second timer here would run unrelated to the first.
        return await handle_tools_call_async(params, context)
    if method == "resources/read":
        return await _with_deadline(handle_resources_read_async(params, context), context)
    if method == "prompts/get":
        return await _with_deadline(handle_prompts_get_async(params, context), context)
    if method == "completion/complete":
        # No async-native sibling: a completer is a small sync callable, and the
        # realistic implementation reads a queryset, which raises
        # ``SynchronousOnlyOperation`` straight from the loop.
        return await _with_deadline(
            sync_to_async(handle_completion_complete, thread_sensitive=True)(params, context),
            context,
        )
    return await sync_to_async(dispatch, thread_sensitive=True)(method, params, context)


async def _with_deadline(coro: Awaitable[Any], context: MCPCallContext) -> Any:
    """Apply the server's dispatch deadline, rendering expiry as a JSON-RPC error.

    Resources and prompts have no per-binding override — the knob lives on tool
    bindings — and no ``isError`` envelope to explain themselves in, so expiry
    is a protocol error rather than a result.
    """
    try:
        return await run_with_deadline(coro, context.config.dispatch_timeout)
    except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct from builtins
        return JsonRpcError(
            JsonRpcErrorCode.SERVER_ERROR,
            "The request exceeded this server's dispatch deadline and was abandoned.",
        )


__all__ = ["adispatch"]
