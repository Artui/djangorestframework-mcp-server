from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import JsonRpcErrorCode
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

    parsed: InitializeParams = InitializeParams.from_payload(params)
    supported: tuple[str, ...] = context.config.protocol_versions
    chosen: str = parsed.protocol_version if parsed.protocol_version in supported else supported[0]

    # The owning server's identity wins: it is resolved once in
    # ``MCPServer.__init__`` (from ``name=``/``version=``, defaulting to
    # ``SERVER_INFO``), so two servers in one project answer ``initialize``
    # with their own names. The settings read below is the degenerate path —
    # a context built without a server, e.g. a hand-wired viewset.
    server_info: Implementation | None = context.server_info
    if server_info is None:
        server_info = build_server_info()
    # One rule for all four: advertise a capability only when the server can
    # answer it. ``prompts`` alone worked this way and ``tools`` / ``resources``
    # were unconditional, which meant a resource-less server still told every
    # client to go and call ``resources/list``. A capability is a promise about
    # what this endpoint does, and the registries are the only honest source
    # for it.
    #
    # ⚠ Deliberately *not* filtered by ``FILTER_LISTINGS_BY_PERMISSIONS``: that
    # decides what a given caller may see, and capabilities describe the
    # server. Making them per-caller would tell an under-privileged client the
    # method does not exist, rather than that it may not use it.
    capabilities = ServerCapabilities(
        tools={} if len(context.tools) > 0 else None,
        resources={} if len(context.resources) > 0 else None,
        prompts={} if len(context.prompts) > 0 else None,
        # The spec's own remedy for an unsupported capability is ``-32601``, so
        # a server that declares ``completions`` and then refuses every request
        # is strictly worse than one that never declared it.
        completions={} if _has_completers(context) else None,
    )
    return InitializeResult(
        protocol_version=chosen,
        capabilities=capabilities,
        server_info=server_info,
        instructions=context.instructions,
    )


def _has_completers(context: MCPCallContext) -> bool:
    """Whether any registered prompt or resource can complete an argument."""
    return any(b.completions for b in context.prompts.all()) or any(
        b.completions for b in context.resources.all()
    )


__all__ = ["handle_initialize"]
