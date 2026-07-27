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

# ``ArgumentBinding`` / ``UnknownArguments`` are re-exported from drf-services:
# ``dispatch_spec`` is the single dispatch core and owns these neutral-core
# policies, so MCP consumes them rather than maintaining a parallel copy. The
# stable import path (``rest_framework_mcp.constants``) is preserved.
from rest_framework_services import ArgumentBinding, UnknownArguments

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


# ---------- Argument binding / unknown-argument policy ----------
#
# ``ArgumentBinding`` (``AUTO`` / ``BUNDLE`` / ``SPREAD_AUTHOR_WINS`` /
# ``SPREAD_CALLER_WINS``) and ``UnknownArguments`` (``IGNORE`` / ``REJECT`` /
# ``PASSTHROUGH``) are imported at the top of this module from drf-services.
# ``dispatch_spec`` owns them as neutral-core policies; MCP service- and
# selector-tool dispatch routes through it and passes the binding's choice.
# Service tools default to ``BUNDLE`` (one validated ``data`` payload) and
# selector tools to ``SPREAD_AUTHOR_WINS`` (spread, ``spec.kwargs`` wins).


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

RESERVED_POOL_SEEDS: frozenset[str] = frozenset(
    {"request", "user", "data", "instance", "serializer"}
)
"""Keys carrying transport-controlled pool seeds.

A client-supplied argument with one of these names would override the
transport's authoritative values (a credential-spoofing footgun). The
spread silently drops them so the pool seeds always win. The dispatched
callable is free to *declare* parameters named ``request`` / ``user`` /
``data`` / ``instance`` / ``serializer``; those receive the pool seeds,
which is the documented sister-repo idiom. ``instance`` (the row resolved
by ``spec.instance_selector_spec``) and ``serializer`` (the bound,
validated input serializer) joined the reserved set with the sister-repo
0.16 adoption.
"""


# ---------- Resource body encoding ----------


class ResourceEncoding(str, Enum):
    """How a resource's selector return value becomes the ``text`` body.

    ``resources/read`` advertises ``mimeType`` from the binding but the body
    encoding is a separate decision, so it is declared separately rather than
    sniffed from the mime type — sniffing would silently change behaviour for
    anyone already advertising a non-JSON type.

    - ``JSON``: pretty-print the value as JSON. The default, and what every
      selector-backed data resource wants.
    - ``TEXT``: the value is already the body. Used for HTML, Markdown, CSV,
      plain text — anything where JSON-encoding would wrap the payload in a
      quoted string literal instead of returning it. The selector must return
      a ``str``.
    """

    JSON = "json"
    TEXT = "text"


# ---------- MCP Apps (interactive UI) ----------

UI_RESOURCE_MIME_TYPE: str = "text/html;profile=mcp-app"
"""Mime type identifying a resource as an interactive HTML view.

Defined by the MCP Apps extension over the base protocol. A host that does
not implement the extension sees an ordinary HTML resource.
"""

UI_META_KEY: str = "ui"
"""The ``_meta`` key MCP Apps owns on a resource or tool descriptor.

``_meta`` is a namespace shared by every extension, each owning one
top-level key; this is Apps'.
"""


class UIPermission(str, Enum):
    """A browser capability an interactive view asks the host to grant.

    The host decides — this only declares what the view would use, in the
    resource's ``_meta.ui.permissions``. Anything not declared is denied by
    the iframe sandbox the host builds.
    """

    CAMERA = "camera"
    MICROPHONE = "microphone"
    GEOLOCATION = "geolocation"
    CLIPBOARD_WRITE = "clipboardWrite"


class UIVisibility(str, Enum):
    """Who may call a tool that is linked to an interactive view.

    Declared per tool in ``_meta.ui.visibility`` and **enforced by the host**,
    which the spec requires not to offer the model a tool whose visibility
    omits ``MODEL``. This server only declares it — nothing here filters
    ``tools/list`` on it, because a client that does not implement the
    extension would not honour the rule anyway.

    - ``MODEL``: the agent may call it — ordinary tool behaviour.
    - ``APP``: the view may call it. An ``APP``-only tool is a fine-grained
      operation that exists to serve the view rather than the conversation.
    """

    MODEL = "model"
    APP = "app"


UI_EXTENSION_ID: str = "io.modelcontextprotocol/ui"
"""Identifier a client uses to advertise MCP Apps support.

Advertisement is **client → server only** — it arrives under
``capabilities.extensions`` in the ``initialize`` request, and the spec
defines no matching server-side capability. Parsed for introspection; nothing
gates on it (see :class:`UIVisibility`).
"""


__all__ = [
    "JSONRPC_VERSION",
    "RESERVED_POOL_SEEDS",
    "RESERVED_POST_FETCH_KEYS",
    "UI_EXTENSION_ID",
    "UI_META_KEY",
    "UI_RESOURCE_MIME_TYPE",
    "ArgumentBinding",
    "JsonRpcErrorCode",
    "JsonRpcId",
    "OutputFormat",
    "ResourceEncoding",
    "ToolKind",
    "UIPermission",
    "UIVisibility",
    "UnknownArguments",
]
