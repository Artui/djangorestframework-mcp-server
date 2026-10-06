from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from django.http import StreamingHttpResponse
from rest_framework_services.types.progress_reporter import ProgressReporter

from rest_framework_mcp.constants import PROGRESS_TOKEN_META_KEY, JsonRpcErrorCode
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import check_permissions, split_url_kwargs
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
    its status before the handler runs, and ``preflight_permissions`` can
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
    a permission check is a pure predicate over the request, the token and the
    route the call names, and both checks are shown the same route.

    **Permissions only, never rate limits.** Consuming a rate limit is not
    idempotent, so pre-flighting one would charge every streamed request twice,
    and buy nothing — a rate-limit rejection is already a ``200`` with the
    detail in the body.

    Returns ``None`` when there is nothing to deny: only ``tools/call`` has a
    binding to check here. That narrowness is safe **because**
    ``can_report_progress`` refuses to stream anything this cannot speak
    for — changing one without the other reopens the hole.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return None
    binding = _tool_binding(params, context)
    if binding is None:
        return None
    # The route the call names, as the handler's own check judges it: a spec
    # permission scoping by ``view.kwargs["project_pk"]`` refused here with a
    # ``403`` the call it was about to admit
    # (``test_the_preflight_sees_the_url_kwargs_the_call_delivers``). Split
    # without refusing a missing kwarg, which the handler still names after the
    # permission has answered
    # (``test_a_denied_caller_missing_a_url_kwarg_is_refused_by_the_preflight``).
    # This runs before the handler validates ``arguments``, so one that is not
    # an object delivers nothing here, as an absent one does there, and the
    # handler is left to name the fault
    # (``test_a_call_with_no_arguments_object_is_judged_on_the_route_defaults``).
    # ``url_kwargs`` is read bare because only a service or a selector binding
    # gets here: a chain declares none, and ``can_report_progress`` refuses to
    # stream one, so the transport never pre-flights it
    # (``test_a_chain_tool_is_not_given_a_stream_it_cannot_use``).
    arguments: Any = params.get("arguments")
    _, delivered_url_kwargs = split_url_kwargs(
        arguments if isinstance(arguments, dict) else {},
        binding.url_kwargs,
        refuse_missing=False,
    )
    allowed, required_scopes = check_permissions(
        binding.permissions,
        context.http_request,
        context.token,
        view_kwargs=delivered_url_kwargs,
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
