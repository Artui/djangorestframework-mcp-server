from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import TASKS_EXTENSION_ID, JsonRpcErrorCode
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.build_server_info import build_server_info
from rest_framework_mcp.protocol.types.implementation import Implementation
from rest_framework_mcp.protocol.types.initialize_params import InitializeParams
from rest_framework_mcp.protocol.types.initialize_result import InitializeResult
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.server_capabilities import ServerCapabilities


def handle_initialize(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> InitializeResult | JsonRpcError:
    """Handle the MCP ``initialize`` request.

    Negotiates protocol version: if the client's requested version is one we
    support, echo it back; otherwise return our latest. Mismatched / unparsable
    params produce ``-32602 Invalid Params`` so the client retries cleanly.
    """
    if not isinstance(params, dict):
        return JsonRpcError(
            code=JsonRpcErrorCode.INVALID_PARAMS,
            message="initialize params must be an object",
        )

    # **Legacy versions only.** ``initialize`` does not exist in ``2026-07-28``,
    # so offering the newest configured version would answer a handshake with a
    # revision in which handshakes were removed, leaving the client speaking a
    # protocol the transport refuses on its next request.
    parsed: InitializeParams = InitializeParams.from_payload(params)
    supported: tuple[str, ...] = context.config.legacy_protocol_versions
    if not supported:
        # A modern-only ``PROTOCOL_VERSIONS`` is a supported configuration, and
        # the natural end state once legacy is dropped.
        return JsonRpcError(
            code=JsonRpcErrorCode.INVALID_PARAMS,
            message=(
                "This server no longer serves the initialize handshake. It supports "
                f"{', '.join(context.config.protocol_versions)}, which carry per-request "
                "metadata instead of negotiating. Send server/discover."
            ),
        )
    chosen: str = parsed.protocol_version if parsed.protocol_version in supported else supported[0]

    # The owning server's identity wins, so two servers in one project answer
    # with their own names. The settings read below is the degenerate path: a
    # context built without a server, e.g. a hand-wired viewset.
    server_info: Implementation | None = context.server_info
    if server_info is None:
        server_info = build_server_info()
    return InitializeResult(
        protocol_version=chosen,
        # ``modern=False`` unconditionally, not from
        # ``context.protocol_version``: reaching this handler *is* the proof.
        # Whoever sent ``initialize`` is a legacy client whatever a header says.
        capabilities=build_capabilities(context, modern=False),
        server_info=server_info,
        instructions=context.instructions,
    )


def build_capabilities(context: MCPCallContext, *, modern: bool) -> ServerCapabilities:
    """What this server can answer, from its registries.

    One rule for all four: advertise a capability only when there is something
    behind it *for this caller*. A capability is a promise about what this
    endpoint does, and the spec's remedy for an unsupported one is ``-32601``,
    so declaring a capability and then refusing every request is worse than
    never declaring it.

    Deliberately **not** filtered by ``FILTER_LISTINGS_BY_PERMISSIONS``: that
    decides what a given caller may see, and capabilities describe the server.
    Making them per-caller would tell an under-privileged client the method does
    not exist, rather than that it may not use it.

    Shared with ``server/discover``, hence ``modern`` — the era is part of
    "behind it", because two of the things advertised here can only be *reached*
    by a modern client:

    - **Every push flag.** The three ``listChanged`` fields and ``subscribe``
      describe notifications that leave through ``subscriptions/listen``, a
      modern-only method. A legacy client acting on ``subscribe`` sends
      ``resources/subscribe`` and gets ``-32601``; one acting on a
      ``listChanged`` waits forever, because nothing answers at all.
      ``resources/subscribe`` is deliberately not built — optional in
      ``2025-11-25``, absent from ``2026-07-28``, whose schema says
      ``SubscriptionFilter.resourceUris`` replaces it.
    - **``extensions``.** Not a field on the legacy ``ServerCapabilities`` at
      all; extension negotiation arrived with ``2026-07-28``, and the tasks
      extension must be declared per request, which a legacy client never does.
    """
    extensions: dict[str, Any] = {}
    if modern and context.tasks is not None and context.task_executor is not None:
        # Both, or neither: a store without an executor creates tasks nothing
        # runs, an executor without a store has nothing to hand over, and either
        # way a client that believes the promise waits for a result forever.
        extensions[TASKS_EXTENSION_ID] = {}

    # A broker to fan out from, *and* a caller who can open a stream to it.
    pushes: bool = modern and context.subscriptions is not None
    return ServerCapabilities(
        tools=_capability(len(context.tools) > 0, listChanged=pushes),
        resources=_capability(len(context.resources) > 0, listChanged=pushes, subscribe=pushes),
        prompts=_capability(len(context.prompts) > 0, listChanged=pushes),
        completions={} if _has_completers(context) else None,
        extensions=extensions or None,
    )


def _capability(present: bool, **flags: bool) -> dict[str, Any] | None:
    """A capability object, or ``None`` when there is nothing behind it.

    ``{}`` still means "supported" — the flags are only added when true, since a
    ``false`` and an omission mean the same thing to a client and emitting one
    invites reading it as a considered decision.
    """
    if not present:
        return None
    return {name: True for name, value in flags.items() if value}


def _has_completers(context: MCPCallContext) -> bool:
    """Whether any registered prompt or resource can complete an argument."""
    return any(b.completions for b in context.prompts.all()) or any(
        b.completions for b in context.resources.all()
    )


__all__ = ["build_capabilities", "handle_initialize"]
