from __future__ import annotations

from dataclasses import dataclass

from rest_framework_mcp.constants import MODERN_PROTOCOL_VERSIONS, OutputFormat


@dataclass(frozen=True)
class MCPConfig:
    """A server's resolved scalar configuration.

    Every field is **already resolved** — there is no "unset" state and no settings
    lookup left to do. [`MCPServer`][rest_framework_mcp.server.mcp_server.MCPServer]
    builds one in ``__init__`` (via ``build_mcp_config``, which reads
    ``REST_FRAMEWORK_MCP``) and threads it to the transport and, on ``MCPCallContext``,
    to every handler. Read at request time these values could only ever be global; read
    once at construction, each server carries its own.

    Do **not** construct this directly to override a field — there are no
    defaults, and a partially-specified config would silently discard the
    project's own ``REST_FRAMEWORK_MCP`` values. Use ``build_mcp_config(
    page_size=50)``, which layers overrides over the settings.

    Each field's user-facing documentation is the matching
    ``REST_FRAMEWORK_MCP`` key in the settings reference; the notes here are
    the invariants a caller of this dataclass has to hold.
    """

    protocol_versions: tuple[str, ...]
    """Supported versions, most-preferred first, across both eras. One list
    rather than two: a version belongs to exactly one era, so splitting the
    setting would let a project configure a contradiction."""

    require_protocol_version_header: bool
    """Whether a non-``initialize`` request must carry a supported
    ``MCP-Protocol-Version``. Present-but-unsupported is rejected either way."""

    sessions_enabled: bool
    """Whether the legacy era mints and requires an ``Mcp-Session-Id``.
    ``False`` is conformant, not a fallback: both legacy revisions say a server
    **MAY** assign one. The modern era is sessionless and ignores this."""

    include_structured_content: bool
    """Whether successful ``tools/call`` results carry ``structuredContent``.
    Per-binding overrides win."""

    include_output_schema: bool
    """Whether ``tools/list`` descriptors carry an ``outputSchema``. Advertising
    one while suppressing ``structuredContent`` violates the spec and is
    rejected at request time. Per-binding overrides win."""

    allowed_origins: tuple[str, ...]
    """``Origin`` allowlist. Empty rejects every cross-origin request; the MCP
    spec makes this check mandatory."""

    default_output_format: OutputFormat
    """Fallback ``output_format`` for tools registered without one."""

    max_request_bytes: int
    """Request-body ceiling; larger bodies get a ``413`` before parsing."""

    max_progress_notifications: int
    """Ceiling on ``notifications/progress`` frames for one request. Past it
    reports are dropped; the dispatch and final response are untouched."""

    max_result_bytes: int | None
    """Ceiling on one tool result or resource read, measured on the encoded wire
    payload so the ``structuredContent`` + text-mirror double emission is
    counted once each. Over it the call returns an ``isError`` result, never a
    truncated payload. ``None`` disables; per-binding overrides win."""

    max_page_size: int | None
    """Ceiling on a ``paginate=True`` selector tool's model-supplied ``limit``,
    advertised as ``maximum`` and clamped at dispatch. ``None`` disables;
    per-binding overrides win."""

    dispatch_timeout: float | None
    """Wall-clock ceiling, in seconds, on one dispatch. **Async transport only**,
    and it does **not** reclaim the worker — a thread in a driver's socket read
    is not interruptible. ``None`` disables; per-binding overrides win."""

    page_size: int
    """Maximum items per list-style call. Clients page with the opaque
    ``cursor``."""

    include_validation_value: bool
    """Whether validation errors echo the offending ``arguments`` under
    ``data.value``. Off by default — the dict can carry PII or secrets."""

    record_service_exceptions: bool
    """Whether a ``ServiceError`` is recorded on the active OpenTelemetry span.
    ``ServiceValidationError`` never is — client input failure, not a fault."""

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
    """How long a client may cache a catalog result — ``server/discover`` and
    the four list methods. ``0`` means "re-fetch every time"."""

    resource_cache_ttl_ms: int
    """The same, for ``resources/read``. A resource body is whatever a selector
    just produced, so a static one opts in with ``cache_ttl_ms=`` instead."""

    task_ttl_ms: int | None
    """How long a created task stays readable. Reported as ``ttlMs`` and
    enforced by the store, so it is both a promise and a bound. ``None`` means
    tasks accumulate until something else evicts them — sound only for a store
    that expires entries itself; the cache-backed one falls back to a week."""

    task_poll_interval_ms: int | None
    """How often the server suggests a client polls ``tasks/get``, or ``None``
    to omit the hint. Advisory: the spec has clients *SHOULD* honour it."""

    subscription_max_seconds: float | None
    """How long one subscription stream may stay open, or ``None`` for no cap.
    Doubles as the re-authorization interval: permissions are checked once, at
    open, so this bounds how long a revoked principal keeps receiving signals."""

    max_concurrent_subscriptions: int | None
    """Ceiling on concurrent subscription streams **per worker**, or ``None``.
    Each parks an ASGI task for its lifetime, so this is what stops an
    authenticated caller exhausting the worker pool."""

    input_request_ttl_seconds: int
    """How long a ``requestState`` stays redeemable. Bounds the replay window on
    the one value here that travels through the client and comes back trusted."""

    max_input_rounds: int
    """How many elicitation rounds one call may take before it fails instead of
    asking again, for a service whose condition the answer never clears."""

    @property
    def modern_protocol_versions(self) -> tuple[str, ...]:
        """The configured versions that carry per-request metadata."""
        return tuple(v for v in self.protocol_versions if v in MODERN_PROTOCOL_VERSIONS)

    @property
    def legacy_protocol_versions(self) -> tuple[str, ...]:
        """The configured versions that negotiate through ``initialize``.

        Not interchangeable with ``protocol_versions``. ``initialize``
        falls back to the first *supported* version when a client omits the
        header, and once a modern revision sits at the head of that list the
        fallback would answer a legacy handshake with a version in which the
        handshake does not exist. Legacy negotiation reads this; modern
        validation reads ``modern_protocol_versions``.
        """
        return tuple(v for v in self.protocol_versions if v not in MODERN_PROTOCOL_VERSIONS)

    @property
    def legacy_fallback_version(self) -> str | None:
        """The version a legacy client is answered with when it names none.

        ``None`` when this server serves **no** legacy revision at all — a
        supported configuration (``PROTOCOL_VERSIONS = ["2026-07-28"]``) and the
        natural end state once legacy is dropped. Callers must branch on
        ``None`` rather than index ``legacy_protocol_versions[0]``, which is an
        ``IndexError`` out of the view on a modern-only server.
        """
        legacy: tuple[str, ...] = self.legacy_protocol_versions
        return legacy[0] if legacy else None


__all__ = ["MCPConfig"]
