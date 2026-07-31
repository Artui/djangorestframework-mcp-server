from __future__ import annotations

from dataclasses import dataclass

from rest_framework_mcp.constants import OutputFormat


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
    """Supported MCP protocol versions, most-preferred first. ``initialize``
    echoes the client's version when supported, else offers ``[0]``."""

    require_protocol_version_header: bool
    """Whether a non-``initialize`` request must carry a supported
    ``MCP-Protocol-Version``. A *present-but-unsupported* header is rejected
    either way — silently downgrading would mask a genuine mismatch."""

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


__all__ = ["MCPConfig"]
