from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.http import HttpRequest
from rest_framework_services.types.progress_reporter import ProgressReporter

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.protocol.types.implementation import Implementation
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.tasks.types.task_executor import TaskExecutor
from rest_framework_mcp.tasks.types.task_store import TaskStore


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

    progress: ProgressReporter | None = None
    """Where this request's progress reports go, or ``None`` for nowhere.

    Populated by the transport only when the client asked — a ``progressToken``
    in the request's ``_meta`` — and only on the async path, which is the one
    that can stream while the dispatch is still running. Handlers forward it to
    ``adispatch_spec`` and it lands in the dispatched callable's kwarg pool as
    the ``progress`` seed.

    ``None`` is the ordinary case and costs nothing: drf-services substitutes
    its no-op reporter, so a service that declares ``progress`` runs unchanged
    whether or not anyone is listening."""

    client_capabilities: dict[str, Any] = field(default_factory=dict)
    """What the client declared **on this request**, verbatim.

    Modern-only by construction: it comes out of the request's ``_meta``, which
    is what a legacy client does not send. So a handler that gates on a
    capability gets ``{}`` for every legacy request and denies — which is the
    correct answer, since a legacy client had no way to declare one per
    request, and the spec forbids relying on a declaration that did not arrive
    with the request."""

    tasks: TaskStore | None = None
    """Where this server's tasks live, or ``None`` if it runs none.

    ``None`` is the ordinary case: a server that has not been given a store and
    an executor does not advertise the tasks extension, never answers with a
    task handle, and treats every task id as unknown."""

    task_executor: TaskExecutor | None = None
    """Where a newly created task is handed off to be worked on.

    Paired with :attr:`tasks` — one without the other cannot run a task, so the
    server treats the extension as unavailable unless it has both."""

    enforce_rate_limits: bool = True
    """Whether a tool's rate limiters are consumed on this dispatch.

    ``True`` everywhere except inside a task worker. A task charges its limits
    once, when the client asks for it, on the request that actually carries the
    caller's address and token — charging them again when the worker replays
    the call would bill one client call twice and halve every configured quota.

    Permissions are *not* treated this way, and the asymmetry is the point:
    testing a permission is a pure predicate that costs nothing to repeat,
    while consuming a quota is a side effect that must happen exactly once."""

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
