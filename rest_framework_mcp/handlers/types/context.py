from __future__ import annotations

from dataclasses import dataclass, field

from django.http import HttpRequest

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.protocol.types.implementation import Implementation
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry


@dataclass(frozen=True)
class MCPCallContext:
    """Bundle of state every JSON-RPC handler needs.

    Constructed by the transport layer per HTTP request and threaded through
    ``dispatch`` to the chosen handler. Frozen so handlers cannot mutate
    shared state — any per-request bookkeeping should happen on locals.
    """

    http_request: HttpRequest
    token: TokenInfo
    tools: ToolRegistry
    resources: ResourceRegistry
    prompts: PromptRegistry
    protocol_version: str
    session_id: str | None = None

    server_info: Implementation | None = None
    """The owning server's identity, echoed by ``initialize``. Instance state,
    resolved once in :meth:`MCPServer.__init__` — so two servers mounted in one
    project introduce themselves differently. ``None`` only for a context built
    without an :class:`~rest_framework_mcp.server.mcp_server.MCPServer` (a
    hand-wired viewset), in which case ``initialize`` falls back to the
    ``SERVER_INFO`` setting."""

    instructions: str | None = None
    """The server's ``description``, surfaced as the spec's ``initialize``
    ``instructions`` field — the only slot the protocol gives a server to
    describe itself to a client. ``None`` omits it from the response."""

    config: MCPConfig = field(default_factory=build_mcp_config)
    """The owning server's resolved scalars, snapshotted in
    :meth:`MCPServer.__init__`. Handlers read these instead of calling
    ``get_setting`` — read per request they could only ever be global, so two
    servers in one project could not differ on any of them.

    The default builds a config from settings, for a context constructed without
    a server (a hand-wired viewset, or a test exercising a handler directly).
    Note this makes the *default* a settings read at construction of the
    context; a context built by :class:`MCPServer` never takes that path."""


__all__ = ["MCPCallContext"]
