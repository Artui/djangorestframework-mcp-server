"""Single-file home for the package's enums and shared constants.

Centralising these here keeps the public type surface obvious at a glance
and lets internal modules import a stable, predictable path rather than
hunting through ``output/format.py`` / ``protocol/json_rpc_error_code.py``
/ ``registry/argument_binding.py`` etc. The top-level
``rest_framework_mcp.__init__`` re-exports the public names so consumers
don't need to know they live here.

Each leaf module that previously held one of these now re-exports from
this file for backward compatibility; new code should import from
``rest_framework_mcp.constants`` directly.
"""

from __future__ import annotations

from enum import Enum, IntEnum

# ---------- JSON-RPC envelope ----------

JSONRPC_VERSION: str = "2.0"
"""The JSON-RPC protocol version this server speaks.

MCP layers on JSON-RPC 2.0; every envelope carries ``"jsonrpc": "2.0"``.
"""

JsonRpcId = str | int | None
"""Type alias for the JSON-RPC ``id`` field.

JSON-RPC 2.0 allows string, integer, or null IDs. Notifications carry
``null``; requests carry a non-null ID and clients correlate responses
back by matching.
"""


class JsonRpcErrorCode(IntEnum):
    """JSON-RPC 2.0 standard error codes plus MCP-specific reservations.

    The standard codes (-32700 through -32600 and -32603) are defined by
    JSON-RPC; MCP reserves the -32000 through -32099 range for server-defined
    errors. We map common MCP failure modes onto stable codes here so handlers
    don't drift.
    """

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # Server-defined (MCP):
    SERVER_ERROR = -32000
    UNAUTHORIZED = -32001
    FORBIDDEN = -32002
    RESOURCE_NOT_FOUND = -32003
    TOOL_NOT_FOUND = -32004
    RATE_LIMITED = -32005


# ---------- Output formatting ----------


class OutputFormat(str, Enum):
    """Output format for the human-readable text block of a ``ToolResult``.

    ``structuredContent`` is always JSON; this enum only controls the
    encoding of the ``content[0]`` text block:

    - ``JSON``: pretty-printed JSON, the safe default.
    - ``TOON``: token-oriented object notation. Compact for large uniform
      arrays; falls back to JSON if the optional ``toon`` extra is not
      installed.
    - ``AUTO``: encoder picks per-payload — TOON for uniform list-of-objects,
      JSON otherwise.
    """

    JSON = "json"
    TOON = "toon"
    AUTO = "auto"

    @classmethod
    def coerce(cls, value: OutputFormat | str | None) -> OutputFormat:
        """Accept either an enum member or its string value; default to JSON."""
        if value is None:
            return cls.JSON
        if isinstance(value, cls):
            return value
        return cls(value)


# ---------- Argument binding ----------


class ArgumentBinding(Enum):
    """How MCP ``arguments`` flow into the kwarg pool of the dispatched callable.

    The pool always carries ``request`` and ``user``. This enum controls
    whether MCP ``arguments`` show up as a single ``data=`` key (the
    historical behavior, well-suited to mutation-shaped services that take
    an ``input_serializer``-validated dict) or are spread as top-level
    pool keys so the callable can declare them as individual parameters
    (the natural shape for selectors and parametric reads).

    Members:

    - ``DATA_ONLY`` — MCP ``arguments`` enter the pool only as
      ``data=<validated-or-raw>``. Default for :class:`ToolBinding`
      (service tools), since mutation services typically take a single
      validated ``data`` payload.
    - ``MERGE`` — every key from the validated MCP arguments (or, when
      no ``input_serializer`` is declared, the raw arguments minus the
      pipeline-reserved keys ``ordering`` / ``page`` / ``limit``) is
      added to the pool. ``spec.kwargs(...)`` output *overrides* on
      conflict — author-declared kwargs win over client-supplied ones, a
      critical invariant for project-scoping selectors. Default for
      :class:`SelectorToolBinding` (selector tools).
    - ``REPLACE`` — like ``MERGE``, but ``spec.kwargs(...)`` *loses* on
      conflict. Useful only when the kwargs provider supplies defaults
      the client is allowed to override.

    The value is internal — never appears on the wire, never coerced
    from a string. ``ArgumentBinding`` is a plain :class:`Enum` (not
    ``str, Enum``); pass the member directly when registering tools.
    """

    DATA_ONLY = "data_only"
    MERGE = "merge"
    REPLACE = "replace"


# ---------- Unknown-argument policy ----------


class UnknownArguments(Enum):
    """How a binding handles MCP ``arguments`` keys not declared in its ``inputSchema``.

    Decouples "fields I want strictly validated" from "fields the client
    may pass through anyway". Built on top of ``input_serializer``, not as
    a replacement — DRF validation still runs on the declared fields in
    every mode.

    Members:

    - ``REJECT`` (default) — the merged ``inputSchema`` advertises
      ``"additionalProperties": false`` and the validator rejects any key
      that isn't part of the binding's known field set. Failure surfaces
      as ``-32602`` with the offending key names in
      ``data.detail["non_field_errors"]``.
    - ``PASSTHROUGH`` — the merged ``inputSchema`` advertises
      ``"additionalProperties": true``; unknown keys survive validation
      and are merged onto the validated dict before binding to the
      callable. Useful when the client sends evolving query args
      (``q`` / ``cursor`` / ``since``) that the spec author wants to
      forward to the callable without restating each in the serializer.
    - ``IGNORE`` — the merged ``inputSchema`` advertises
      ``"additionalProperties": true``, but unknown keys are dropped
      after validation. Forward-compatibility mode: older clients can
      still send fields newer servers haven't formalised yet, and the
      server silently accepts and ignores them.

    Reserved transport-controlled keys (``request`` / ``user`` / ``data``)
    and selector-tool post-fetch keys (``ordering`` / ``page`` /
    ``limit``) are never considered "unknown" in any mode — they're
    handled by the dispatch pipeline, not the validator.

    Plain :class:`Enum`, same discipline as :class:`ArgumentBinding`:
    internal-only value, no string coercion at API boundaries.
    """

    REJECT = "reject"
    PASSTHROUGH = "passthrough"
    IGNORE = "ignore"


# ---------- Tool kind discriminator ----------


class ToolKind(Enum):
    """Discriminator for :class:`ToolDefinition` and the
    :func:`register_tools` dispatch table.

    Internal-only — never appears on the wire. Members map directly to
    the two registration entry points on :class:`MCPServer`:

    - ``SERVICE`` → :meth:`MCPServer.register_service_tool`
    - ``SELECTOR`` → :meth:`MCPServer.register_selector_tool`

    Use :meth:`ToolDefinition.service` / :meth:`ToolDefinition.selector`
    instead of constructing :class:`ToolDefinition` with this kwarg
    directly — the classmethods are the typed entry points.
    """

    SERVICE = "service"
    SELECTOR = "selector"


# ---------- Reserved kwarg-pool keys (shared across handlers) ----------

RESERVED_POST_FETCH_KEYS: frozenset[str] = frozenset({"ordering", "page", "limit"})
"""Keys consumed by the selector-tool post-fetch pipeline.

FilterSet, ordering, and pagination read these out of the MCP arguments
dict directly; they must not also leak into the kwarg pool of the
dispatched selector, or the selector would receive surprise kwargs it
never declared.
"""

RESERVED_POOL_SEEDS: frozenset[str] = frozenset({"request", "user", "data"})
"""Keys carrying transport-controlled pool seeds.

A client-supplied argument with one of these names would override the
transport's authoritative values (a credential-spoofing footgun). The
spread silently drops them so the pool seeds always win. The dispatched
callable is free to *declare* parameters named ``request`` / ``user`` /
``data``; those receive the pool seeds, which is the documented
sister-repo idiom.
"""


__all__ = [
    "JSONRPC_VERSION",
    "RESERVED_POOL_SEEDS",
    "RESERVED_POST_FETCH_KEYS",
    "ArgumentBinding",
    "JsonRpcErrorCode",
    "JsonRpcId",
    "OutputFormat",
    "ToolKind",
    "UnknownArguments",
]
