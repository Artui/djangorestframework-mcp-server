from __future__ import annotations

from dataclasses import dataclass

from rest_framework_mcp.constants import MODERN_PROTOCOL_VERSIONS, OutputFormat


@dataclass(frozen=True)
class MCPConfig:
    """A server's resolved scalar configuration.

    Every field is **already resolved** — there is no "unset" state and no
    settings lookup left to do. :class:`MCPServer` builds one in ``__init__``
    (via :func:`~rest_framework_mcp.config.build_mcp_config.build_mcp_config`,
    which reads ``REST_FRAMEWORK_MCP``) and threads it to the transport and, on
    :class:`~rest_framework_mcp.handlers.types.context.MCPCallContext`, to every
    handler.

    That indirection is the point: read at request time, these values could only
    ever be global, so two servers in one project could not differ on any of
    them. Read once at construction, each server carries its own — and a project
    that configures nothing still gets the settings-derived defaults.

    Do **not** construct this directly to override a field: writing
    ``MCPConfig(page_size=50)`` would be impossible anyway (no defaults, by
    design), and a partially-specified config would silently discard the
    project's own ``REST_FRAMEWORK_MCP`` values. Use ``build_mcp_config(
    page_size=50)``, which layers overrides over the settings.
    """

    protocol_versions: tuple[str, ...]
    """Supported MCP protocol versions, most-preferred first, across both eras.

    What ``server/discover`` reports as ``supportedVersions``. The two eras are
    served from this one list rather than two, because a version belongs to
    exactly one era and splitting the setting would let a project configure a
    contradiction."""

    require_protocol_version_header: bool
    """Whether a non-``initialize`` request must carry a supported
    ``MCP-Protocol-Version``. A *present-but-unsupported* header is rejected
    either way — silently downgrading would mask a genuine mismatch."""

    sessions_enabled: bool
    """Whether the legacy era mints and requires an ``Mcp-Session-Id``.

    ``False`` runs the handshake era statelessly: no id at ``initialize``, none
    required afterwards, ``405`` for the SSE ``GET`` and the session ``DELETE``.
    A conformant mode — both legacy revisions say a server **MAY** assign a
    session ID, and bind the client's obligation to it having done so. The
    modern era is sessionless regardless and ignores this."""

    include_structured_content: bool
    """Whether successful ``tools/call`` results carry ``structuredContent``
    alongside the human-readable text. Per-binding overrides win."""

    include_output_schema: bool
    """Whether ``tools/list`` descriptors carry an ``outputSchema``. Advertising
    a schema while suppressing ``structuredContent`` is a spec violation and is
    rejected at request time. Per-binding overrides win."""

    allowed_origins: tuple[str, ...]
    """``Origin`` allowlist. Empty rejects every cross-origin request; the MCP
    spec makes this check mandatory."""

    default_output_format: OutputFormat
    """Fallback ``output_format`` for tools registered without one."""

    max_request_bytes: int
    """Request-body ceiling; larger bodies get a ``413`` before parsing."""

    max_progress_notifications: int
    """Ceiling on ``notifications/progress`` frames emitted for one request.

    The spec asks both parties to rate-limit progress. Past the cap further
    reports are dropped; the dispatch is untouched and the final response still
    arrives."""

    max_result_bytes: int | None
    """Outbound mirror of :attr:`max_request_bytes`: ceiling on one tool result
    or resource read, measured on the encoded wire payload (so the
    ``structuredContent`` + text-mirror double-emission is counted once each,
    which is what reaches the client's context). Over it, the call returns an
    ``isError`` result naming the remedy — never a silently truncated payload.
    ``None`` disables. Per-binding overrides win."""

    max_page_size: int | None
    """Ceiling on the model-supplied ``limit`` of a ``paginate=True`` selector
    tool, advertised as ``maximum`` on the generated schema and clamped at
    dispatch. Safe to clamp rather than error because ``hasNext`` /
    ``totalPages`` keep a clamped page self-describing. ``None`` disables.
    Per-binding overrides win."""

    dispatch_timeout: float | None
    """Wall-clock ceiling, in seconds, on one dispatch. ⚠ **Async transport
    only**, and it does **not** reclaim the worker — a thread in a driver's
    socket read is not interruptible by asyncio cancellation. It buys a
    terminal protocol event, and pairs with a database statement timeout rather
    than replacing it. ``None`` disables. Per-binding overrides win."""

    page_size: int
    """Maximum items per list-style call (``tools/list``, ``resources/list``,
    ``prompts/list``). Clients page with the opaque ``cursor``."""

    include_validation_value: bool
    """Whether validation errors echo the offending ``arguments`` under
    ``data.value``. Off by default — the dict can carry PII or secrets."""

    record_service_exceptions: bool
    """Whether a ``ServiceError`` is recorded on the active OpenTelemetry span
    before being mapped to a tool result. ``ServiceValidationError`` never is —
    it is client input failure, not a server fault."""

    filter_listings_by_permissions: bool
    """Whether list-style calls hide bindings the caller can't invoke.
    Per-binding ``always_listed=True`` opts back in."""

    require_tool_permissions: bool
    """Whether registering a tool with no permissions raises instead of warning.
    Read at *registration* time, not per request."""

    require_tool_descriptions: bool
    """Whether registering a tool with no description raises instead of warning.
    Read at *registration* time, not per request."""

    require_list_pagination: bool
    """Whether registering an unpaginated LIST selector tool raises instead of
    warning. Read at *registration* time, not per request."""

    catalog_cache_ttl_ms: int
    """How long, in milliseconds, a client may cache a catalog result —
    ``server/discover`` and the four list methods. ``0`` means "immediately
    stale, re-fetch every time".

    Catalogs are fixed once a process boots (registration happens at
    configuration time), so the honest ceiling is "until the next deploy" —
    which nothing here can know. The default is a short window that costs a
    client one stale minute after a release rather than a stale catalog until
    it reconnects."""

    resource_cache_ttl_ms: int
    """The same, for ``resources/read``. Defaults to ``0``: a resource body is
    whatever a selector just produced, and caching live data by default would
    serve yesterday's invoice. A genuinely static resource — an interactive
    view, a rendered document — sets ``cache_ttl_ms=`` at registration."""

    task_ttl_ms: int | None
    """How long a created task stays readable, in milliseconds; ``None`` for no
    expiry.

    Reported to the client as ``ttlMs`` and enforced by the store, so it is both
    a promise and a bound: after it elapses the record may be dropped, and a
    client that has not finished polling gets "unknown task". The default is
    generous, because the failure it guards against — a queue backlog outliving
    the window — looks to a client exactly like work that vanished.

    ⚠ ``None`` means tasks accumulate until something else evicts them. Sound
    only for a store that expires entries on its own; the cache-backed one
    falls back to a week so an un-polled task cannot pin memory forever."""

    task_poll_interval_ms: int | None
    """How often the server suggests a client polls ``tasks/get``, or ``None``
    to omit the hint.

    Advisory — the spec has clients *SHOULD* honour it and lets servers
    rate-limit anyone who does not. Worth setting close to how long the work
    actually takes: too low and every task costs a stream of no-op polls, too
    high and a fast task sits finished while its client waits."""

    subscription_max_seconds: float | None
    """How long one subscription stream may stay open, or ``None`` for no cap.

    ⚠ Doubles as the re-authorization interval: a subscription's permissions are
    checked once, at open, so the cap is what bounds how long a revoked
    principal keeps receiving change signals."""

    max_concurrent_subscriptions: int | None
    """Ceiling on concurrent subscription streams **per worker**, or ``None``.

    Each one parks an ASGI task for its lifetime, so this is what stops an
    authenticated caller exhausting the worker pool by opening streams in a
    loop."""

    input_request_ttl_seconds: int
    """How long a ``requestState`` handed to a client stays redeemable.

    ⚠ Bounds the replay window on the one value in this protocol that travels
    through the client and comes back trusted. Raise it for forms a human might
    leave open; do not raise it to work around a client that loses tokens."""

    max_input_rounds: int
    """How many elicitation rounds one call may take before it fails instead of
    asking again.

    Guards against a service whose condition the answer never clears — the
    failure mode where client and server keep re-asking a user the same
    question. A service that genuinely needs several answers should be under
    this, not above it."""

    @property
    def modern_protocol_versions(self) -> tuple[str, ...]:
        """The configured versions that carry per-request metadata."""
        return tuple(v for v in self.protocol_versions if v in MODERN_PROTOCOL_VERSIONS)

    @property
    def legacy_protocol_versions(self) -> tuple[str, ...]:
        """The configured versions that negotiate through ``initialize``.

        ⚠ Not interchangeable with :attr:`protocol_versions`. ``initialize``
        falls back to the first *supported* version when a client omits the
        header, and once a modern revision sits at the head of the list that
        fallback would answer a legacy handshake with a version in which the
        handshake does not exist. Legacy negotiation reads this; modern
        validation reads :attr:`modern_protocol_versions`.
        """
        return tuple(v for v in self.protocol_versions if v not in MODERN_PROTOCOL_VERSIONS)

    @property
    def legacy_fallback_version(self) -> str | None:
        """The version a legacy client is answered with when it names none.

        ``None`` when this server serves **no** legacy revision at all — which
        is a supported configuration (``PROTOCOL_VERSIONS = ["2026-07-28"]``)
        and the natural end state once legacy is dropped.

        ⚠ **Exists because indexing ``legacy_protocol_versions[0]`` was a 500.**
        Two call sites reached for the first legacy version as "the default",
        and on a modern-only server that tuple is empty: every ``initialize``,
        and every header-less ``server/discover``, raised ``IndexError`` out of
        the view. Nothing validated the setting either, so the failure appeared
        only in production traffic. Callers now branch on ``None`` and answer
        with something a client can act on.
        """
        legacy: tuple[str, ...] = self.legacy_protocol_versions
        return legacy[0] if legacy else None


__all__ = ["MCPConfig"]
