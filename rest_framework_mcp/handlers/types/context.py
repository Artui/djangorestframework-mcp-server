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
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.tasks.types.task_executor import TaskExecutor
from rest_framework_mcp.tasks.types.task_store import TaskStore


@dataclass(frozen=True)
class MCPCallContext:
    """Bundle of state every JSON-RPC handler needs.

    Constructed by the transport layer per HTTP request and threaded through
    ``dispatch`` to the chosen handler. Frozen so handlers cannot mutate shared
    state — per-request bookkeeping happens on locals.
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
    resolved once in ``MCPServer.__init__``, so two servers mounted in one
    project introduce themselves differently. ``None`` only for a context built
    without an [`MCPServer`][rest_framework_mcp.server.mcp_server.MCPServer], in which
    case ``initialize`` falls back to the ``SERVER_INFO`` setting."""

    instructions: str | None = None
    """The server's ``description``, surfaced as ``initialize``'s
    ``instructions`` — the only slot the protocol gives a server to describe
    itself. ``None`` omits it."""

    progress: ProgressReporter | None = None
    """Where this request's progress reports go, or ``None`` for nowhere.

    Populated either by the async transport when the client asked (a
    ``progressToken`` in ``_meta``, reported down the open stream), or by a task
    worker, always, writing onto the task record for the client to poll. Either
    way it lands in the dispatched callable's kwarg pool as the ``progress``
    seed and the service body cannot tell which it got.

    ``None`` costs nothing: drf-services substitutes its no-op reporter, so a
    service declaring ``progress`` runs unchanged with nobody listening."""

    client_capabilities: dict[str, Any] = field(default_factory=dict)
    """What the client declared **on this request**, verbatim.

    Modern-only by construction: it comes out of the request's ``_meta``, which
    a legacy client does not send. A handler gating on a capability therefore
    gets ``{}`` for every legacy request and denies, which is correct — the spec
    forbids relying on a declaration that did not arrive with the request."""

    tasks: TaskStore | None = None
    """Where this server's tasks live, or ``None`` if it runs none. Without a
    store the server does not advertise the tasks extension, never answers with
    a task handle, and treats every task id as unknown."""

    task_executor: TaskExecutor | None = None
    """Where a newly created task is handed off. Paired with ``tasks`` — one
    without the other cannot run a task, so the extension stays unavailable
    unless the server has both."""

    subscriptions: SubscriptionBroker | None = None
    """Where server-pushed notifications fan out, or ``None`` if this server
    pushes none. Without a broker the server advertises no subscription
    capabilities and answers ``subscriptions/listen`` with an empty grant, so a
    client learns immediately rather than holding a silent stream open."""

    enforce_rate_limits: bool = True
    """Whether a tool's rate limiters are consumed on this dispatch.

    ``True`` everywhere except inside a task worker. A task charges its limits
    once, on the request that actually carries the caller's address and token;
    charging again on replay would bill one client call twice and halve every
    configured quota. Permissions are deliberately not treated this way —
    testing a predicate costs nothing to repeat, consuming a quota is a side
    effect that must happen exactly once."""

    config: MCPConfig = field(default_factory=build_mcp_config)
    """The owning server's resolved scalars, snapshotted in
    ``MCPServer.__init__``. Handlers read these instead of calling
    ``get_setting``, which could only ever be global — two servers in one
    project could not otherwise differ on any of them.

    The default builds a config from settings for a context constructed without
    a server (a hand-wired viewset, or a test driving a handler directly), which
    does make the *default* a settings read at construction time; a context
    built by [`MCPServer`][rest_framework_mcp.server.mcp_server.MCPServer] never takes that path."""


__all__ = ["MCPCallContext"]
