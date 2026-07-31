from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import StreamingHttpResponse
from rest_framework_services.types.progress_reporter import ProgressReporter

from rest_framework_mcp.constants import PROGRESS_TOKEN_META_KEY, JsonRpcErrorCode
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import check_permissions
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.response_stream import build_response_stream


def progress_token(params: Any) -> str | int | None:
    """The token by which a client asked to hear about this request's progress.

    ⭐ Era-independent: ``_meta.progressToken`` sits in the same place in
    ``2025-11-25`` and ``2026-07-28``, so a legacy client gets streamed
    progress on the same terms as a modern one. One of the few things the
    transport fork does not have to branch on.

    A non-string, non-integer token is treated as absent rather than rejected.
    The spec constrains the type but a server **MAY** decline to send progress
    at all, so declining is always a legal answer — and a stricter reading
    would fail a request over a field that only affects an optional courtesy.
    """
    if not isinstance(params, dict):
        return None
    meta: Any = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    token: Any = meta.get(PROGRESS_TOKEN_META_KEY)
    # ``bool`` is an ``int`` subclass and is plainly not a token.
    if isinstance(token, bool) or not isinstance(token, str | int):
        return None
    return token


def preflight_permissions(method: str, params: Any, context: MCPCallContext) -> JsonRpcError | None:
    """Run a tool's permission stack *before* deciding to stream.

    ⚠ **The one thing streaming would otherwise cost.** A
    ``StreamingHttpResponse`` commits its status before the dispatch runs, so a
    permission denial discovered inside the handler could only ride as an
    in-stream error inside a ``200`` — losing the ``403`` the MCP authorization
    spec makes normative, and the ``WWW-Authenticate`` challenge that tells the
    client what to ask for instead.

    Checking here first restores it. Safe to run twice because a permission
    check is a pure predicate over ``(request, token)``; the handler runs its
    own again and reaches the same answer.

    ⚠ **Permissions only, never rate limits.** Consuming a rate limit is not
    idempotent, so pre-flighting one would charge every streamed request twice.
    They stay inside the handler — and cost nothing, because a rate-limit
    rejection was already a ``200`` with the detail in the body.

    Returns ``None`` when there is nothing to deny: only ``tools/call`` has a
    binding to check at this point, and an unknown tool is the handler's error
    to report.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return None
    name: Any = params.get("name")
    if not isinstance(name, str):
        return None
    binding = context.tools.get(name)
    if binding is None:
        return None
    allowed, required_scopes = check_permissions(
        binding.permissions, context.http_request, context.token
    )
    if allowed:
        return None
    return JsonRpcError(
        JsonRpcErrorCode.FORBIDDEN,
        "Insufficient permission",
        data={"requiredScopes": required_scopes} if required_scopes else None,
    )


def stream_with_progress(
    *,
    dispatch: Callable[[ProgressReporter], Awaitable[Any]],
    request_id: Any,
    token: str | int,
    context: MCPCallContext,
) -> StreamingHttpResponse:
    """Answer this request with a progress-carrying SSE stream."""
    return build_response_stream(
        dispatch=dispatch,
        request_id=request_id,
        progress_token=token,
        max_notifications=context.config.max_progress_notifications,
    )


__all__ = ["preflight_permissions", "progress_token", "stream_with_progress"]
