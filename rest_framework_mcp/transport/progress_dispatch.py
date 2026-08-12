from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import StreamingHttpResponse
from rest_framework_services.types.progress_reporter import ProgressReporter

from rest_framework_mcp.constants import PROGRESS_TOKEN_META_KEY, JsonRpcErrorCode
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import check_permissions
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.transport.response_stream import build_response_stream


def progress_token(params: Any) -> str | int | None:
    """The token by which a client asked to hear about this request's progress.

    Era-independent: ``_meta.progressToken`` sits in the same place in
    ``2025-11-25`` and ``2026-07-28``, so the transport does not branch here.

    A non-string, non-integer token is treated as absent rather than rejected.
    The spec constrains the type, but a server **MAY** decline to send progress
    at all, so declining is always legal, while rejecting would fail a request
    over a field that only affects an optional courtesy.
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


def can_report_progress(method: str, params: Any, context: MCPCallContext) -> bool:
    """Whether this request's dispatch can actually emit progress.

    Narrower than "the client asked", and deliberately so. Opening a stream for
    a dispatch that will never report costs a connection, buys nothing, and
    silently gives up the normative ``403``: a ``StreamingHttpResponse`` commits
    its status before the handler runs, and :func:`preflight_permissions` can
    only speak for ``tools/call``. So streaming is confined to the paths that
    actually thread a reporter — ``tools/call`` on a service or selector
    binding.

    ``resources/read`` and ``prompts/get`` never receive one, and chain tools
    build their own kwarg pool with no ``progress`` seed. This gate is where
    reporting gets re-enabled once one is threaded through.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return False
    binding = _tool_binding(params, context)
    return binding is not None and not isinstance(binding, ChainToolBinding)


def preflight_permissions(method: str, params: Any, context: MCPCallContext) -> JsonRpcError | None:
    """Run a tool's permission stack *before* deciding to stream.

    A ``StreamingHttpResponse`` commits its status before the dispatch runs, so
    a denial discovered inside the handler could only ride as an in-stream
    error inside a ``200`` — losing the ``403`` the MCP authorization spec
    makes normative and the ``WWW-Authenticate`` challenge. Safe to run twice:
    a permission check is a pure predicate over ``(request, token)``.

    **Permissions only, never rate limits.** Consuming a rate limit is not
    idempotent, so pre-flighting one would charge every streamed request twice,
    and buy nothing — a rate-limit rejection is already a ``200`` with the
    detail in the body.

    Returns ``None`` when there is nothing to deny: only ``tools/call`` has a
    binding to check here. That narrowness is safe **because**
    :func:`can_report_progress` refuses to stream anything this cannot speak
    for — changing one without the other reopens the hole.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return None
    binding = _tool_binding(params, context)
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


def _tool_binding(params: dict[str, Any], context: MCPCallContext) -> Any:
    """The binding a ``tools/call`` names, or ``None`` if it names none.

    Shared so the streaming gate and the permission pre-flight resolve the
    *same* binding: two lookups that disagreed would be the exact bug this
    pairing exists to prevent.
    """
    name: Any = params.get("name")
    if not isinstance(name, str):
        return None
    return context.tools.get(name)


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


__all__ = [
    "can_report_progress",
    "preflight_permissions",
    "progress_token",
    "stream_with_progress",
]
