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

    # ⚠ **Legacy versions only.** ``initialize`` is a legacy-era method — it
    # does not exist in ``2026-07-28`` — so offering the newest configured
    # version here would answer a handshake with a revision in which handshakes
    # were removed, and the client would then speak a protocol the transport
    # would refuse on its next request.
    parsed: InitializeParams = InitializeParams.from_payload(params)
    supported: tuple[str, ...] = context.config.legacy_protocol_versions
    if not supported:
        # ⚠ A modern-only ``PROTOCOL_VERSIONS`` is a supported configuration and
        # the natural end state once legacy is dropped — this used to index an
        # empty tuple and 500. Told plainly, a legacy client learns that the
        # handshake era is gone and which revisions replaced it, which is
        # something it can report to a human; a 500 is not.
        return JsonRpcError(
            code=JsonRpcErrorCode.INVALID_PARAMS,
            message=(
                "This server no longer serves the initialize handshake. It supports "
                f"{', '.join(context.config.protocol_versions)}, which carry per-request "
                "metadata instead of negotiating. Send server/discover."
            ),
        )
    chosen: str = parsed.protocol_version if parsed.protocol_version in supported else supported[0]

    # The owning server's identity wins: it is resolved once in
    # ``MCPServer.__init__`` (from ``name=``/``version=``, defaulting to
    # ``SERVER_INFO``), so two servers in one project answer ``initialize``
    # with their own names. The settings read below is the degenerate path —
    # a context built without a server, e.g. a hand-wired viewset.
    server_info: Implementation | None = context.server_info
    if server_info is None:
        server_info = build_server_info()
    return InitializeResult(
        protocol_version=chosen,
        # ⚠ ``modern=False`` unconditionally, and not from
        # ``context.protocol_version``: reaching this handler *is* the proof.
        # ``initialize`` does not exist in ``2026-07-28``, so whoever sent it is
        # a legacy client whatever any header says.
        capabilities=build_capabilities(context, modern=False),
        server_info=server_info,
        instructions=context.instructions,
    )


def build_capabilities(context: MCPCallContext, *, modern: bool) -> ServerCapabilities:
    """What this server can answer, from its registries.

    One rule for all four: advertise a capability only when there is something
    behind it. ``prompts`` alone worked this way and ``tools`` / ``resources``
    were unconditional, which meant a resource-less server still told every
    client to go and call ``resources/list``. A capability is a promise about
    what this endpoint does, and the registries are the only honest source for
    it. The spec's own remedy for an unsupported capability is ``-32601``, so a
    server that declares one and then refuses every request is strictly worse
    than one that never declared it.

    ⚠ Deliberately **not** filtered by ``FILTER_LISTINGS_BY_PERMISSIONS``: that
    decides what a given caller may see, and capabilities describe the server.
    Making them per-caller would tell an under-privileged client the method does
    not exist, rather than that it may not use it.

    Shared with ``server/discover``, which reports the same bundle for a caller
    in the same era — hence ``modern``, which is **not** a stylistic branch. Two
    of the things advertised here can only be *reached* by a modern client, and
    telling a legacy one about them is a promise nothing on its path can keep:

    - **Every push flag.** All three ``listChanged`` fields and ``subscribe``
      describe notifications that leave through ``subscriptions/listen``, which
      is a modern-only method. A legacy client acting on ``subscribe`` sends
      ``resources/subscribe`` and gets ``-32601``; one acting on a
      ``listChanged`` gets something worse, because nothing answers at all and
      it simply waits. ⛔ ``resources/subscribe`` is deliberately not built: it
      is optional in ``2025-11-25``, absent from ``2026-07-28`` (the schema says
      ``SubscriptionFilter.resourceUris`` *"replaces the former
      resources/subscribe RPC"*), and implementing it would mean a cross-process
      session→URI registry serving only the era being carried for compatibility.
    - **``extensions``.** Not a field on the legacy ``ServerCapabilities`` at
      all — extension negotiation arrived with ``2026-07-28``. The tasks
      extension is unreachable for a legacy client anyway, since it must be
      declared per request and a legacy client declares nothing per request.

    ⚠ The rule these follow is the same one the registries follow: advertise a
    capability only when there is something behind it *for this caller*. The
    era is part of "behind it".
    """
    extensions: dict[str, Any] = {}
    if modern and context.tasks is not None and context.task_executor is not None:
        # ⚠ Both, or neither. A store without an executor can create a task
        # nothing will ever run, and an executor without a store has nothing to
        # hand over — either way the promise is false, and a client that
        # believes it will wait for a result that never comes.
        extensions[TASKS_EXTENSION_ID] = {}

    # A broker to fan out from, *and* a caller who can open a stream to it.
    # Without the first nothing would ever be published; without the second
    # there is no method to publish it down.
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
