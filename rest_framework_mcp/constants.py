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
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS

"""Keys carrying transport-controlled pool seeds — re-exported from the sister
repo, which owns the set. A client-supplied argument with one of these names
would override the transport's authoritative values (a credential-spoofing
footgun), so the spread silently drops them. The dispatched callable is free to
*declare* a parameter of that name; it receives the seed, the documented idiom.

Previously a local copy, which had silently fallen a key behind the set it
mirrored (``collection``). The stable import path is preserved.
"""

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
    JSON-RPC. MCP then partitions the server-error range: ``-32000..-32019``
    is implementation-defined, ``-32020..-32099`` is reserved for the spec
    itself. Everything we allocate below sits in the implementation-defined
    half — with one exception, and it is the important one.

    ⚠ **``-32002`` is not ours to allocate.** The MCP resources spec names it
    for "Resource not found", and the ``2026-07-28`` revision singles it out
    as the one legacy code clients should keep recognising. A server that
    spends it on something else is not merely unconventional: a spec-following
    client reads that error as a missing resource. It therefore belongs to
    :attr:`RESOURCE_NOT_FOUND` here and nothing else.

    Two codes are **burned** rather than reused. ``-32003`` and ``-32004``
    were this package's own not-found codes before the wire values were
    aligned with the spec; a client written against an older release still
    reads them as "resource/prompt not found" and "unknown tool". Re-issuing
    either for a different condition would silently mislead exactly the
    clients that took the trouble to special-case them, so the next
    implementation-defined code allocated is ``-32006``.
    """

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # Server-defined (MCP):
    SERVER_ERROR = -32000
    UNAUTHORIZED = -32001
    RESOURCE_NOT_FOUND = -32002
    # -32003, -32004: burned — see the class docstring.
    RATE_LIMITED = -32005
    FORBIDDEN = -32006
    # Spec-reserved (``-32020..-32099``, allocated sequentially by the MCP
    # specification itself). These are not ours to number or to repurpose.
    HEADER_MISMATCH = -32020
    MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
    UNSUPPORTED_PROTOCOL_VERSION = -32022


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


MODERN_PROTOCOL_VERSIONS: frozenset[str] = frozenset({"2026-07-28"})
"""Protocol revisions that carry version, identity and capabilities per request.

The spec's own split: **modern** revisions (``2026-07-28`` and later) declare
everything on each request and hold no session; **legacy** ones (``2025-11-25``
and earlier) negotiate once through ``initialize``. Everything this package
branches on era for reads from this set, so adding a revision is one edit.

A server may serve both concurrently on one endpoint, which is what this
package does — legacy clients have no fall-forward mechanism, so dropping them
would strand every client that has not migrated with nothing but an error
string to go on.
"""

PROTOCOL_VERSION_META_KEY: str = "io.modelcontextprotocol/protocolVersion"
"""Per-request ``_meta`` key naming the revision a modern request speaks.

⭐ **This is the era discriminator.** Its presence is what tells a dual-era
server it is talking to a modern client — not the ``MCP-Protocol-Version``
header, which legacy clients have sent since ``2025-06-18``, and not the
method, since most methods exist in both eras.
"""

CLIENT_INFO_META_KEY: str = "io.modelcontextprotocol/clientInfo"
"""Per-request ``_meta`` key carrying the client's self-reported identity.

Optional, and — like the server's own — unverified. Parsed for introspection
and logging; nothing branches on it.
"""

CLIENT_CAPABILITIES_META_KEY: str = "io.modelcontextprotocol/clientCapabilities"
"""Per-request ``_meta`` key declaring what the client supports.

Required on a modern request, and empty (``{}``) is a valid declaration. What
replaces the ``initialize`` handshake's one-time capability exchange: a server
**MUST NOT** rely on a capability the client did not declare *on that request*.
"""

PROGRESS_TOKEN_META_KEY: str = "progressToken"
"""Per-request ``_meta`` key by which a client asks to be told about progress.

⭐ **Unprefixed, and identical in both eras** — ``2025-11-25`` puts it in the
request's ``_meta`` and ``2026-07-28`` keeps it there alongside the new
protocol fields. So progress is one of the few things this package does not
have to branch on era for: a legacy client gets it on the same terms.

Its presence is also the *only* signal that streaming is wanted. A server MAY
decline to send notifications at all, so a request without the token is
answered with a single JSON object rather than a stream that would carry one
event.
"""

SESSIONLESS_METHODS: frozenset[str] = frozenset({"initialize", "server/discover"})
"""Methods answerable before a session exists.

The legacy transport requires an ``Mcp-Session-Id`` on everything except the
request that mints one. ``server/discover`` has to join it: its whole purpose
is to be the first thing a client sends — a modern client has no handshake to
run and nothing to present — so gating it behind a session would leave it
reachable only by clients that did not need it.

They are exempt from the ``MCP-Protocol-Version`` header requirement for the
same reason, and only ``initialize`` mints a session. Discovery creates no
state; that is the point of it.
"""

SERVER_INFO_META_KEY: str = "io.modelcontextprotocol/serverInfo"
"""Reserved ``_meta`` key carrying the server's self-reported identity.

Where ``server/discover`` puts what ``initialize`` returned as a top-level
``serverInfo``. The move is deliberate on the spec's part: the value is
unverified, and clients are told not to change behaviour or make security
decisions from it, so it sits in the metadata namespace rather than looking
like negotiated protocol state.
"""


# ---------- Result envelope ----------


class ResultType(str, Enum):
    """The discriminator every result carries from ``2026-07-28`` onward.

    A **MUST** for servers implementing that revision, and harmless before it:
    a legacy result object is an open shape, and a client on an older revision
    is told to read an absent ``resultType`` as ``complete``. So this is
    emitted unconditionally rather than era-branched — one less thing for the
    transport fork to have to get right.

    ``INPUT_REQUIRED`` is the other value the spec defines: a result that asks
    the client for input and expects the original request to be retried with
    the answers. Nothing here produces one yet — it is the shape elicitation
    will take when it lands — but the vocabulary is the spec's, not ours, so
    both members live here.
    """

    COMPLETE = "complete"
    INPUT_REQUIRED = "input_required"


class CacheScope(str, Enum):
    """How widely a cacheable result may be reused, per ``Cache-Control``.

    ⚠ **Derived, never configured.** ``PUBLIC`` licenses any intermediary — a
    shared gateway, a caching proxy — to serve this response *across
    authorization contexts*. A result that varies by caller and is labelled
    ``PUBLIC`` is a cross-tenant disclosure with a cache in front of it, which
    is precisely the kind of mistake a settings knob invites. The handlers work
    it out from what actually shaped the response instead: a permission-filtered
    listing is ``PRIVATE``, an unfiltered one is ``PUBLIC``, and a resource body
    a selector produced for this caller is always ``PRIVATE``.
    """

    PUBLIC = "public"
    PRIVATE = "private"


# ---------- Argument completion ----------

MAX_COMPLETION_VALUES: int = 100
"""The spec's hard cap on ``values`` in one ``completion/complete`` result.

A completer is free to return more; the handler slices to this and sets
``hasMore``, so the cap is never something a consumer has to remember.
"""


# ---------- Display metadata ----------


class IconTheme(str, Enum):
    """Which background an :class:`~rest_framework_mcp.protocol.types.icon.Icon`
    was designed for.

    Omitting the theme (``None``) tells the client the icon works on either,
    which is the right answer for most artwork. Declare it only when you are
    shipping a light/dark pair.
    """

    LIGHT = "light"
    DARK = "dark"


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
    - ``BLOB``: the value is binary. The selector returns ``bytes`` and the
      body is base64-encoded into the spec's ``blob`` field instead of
      ``text`` — the two are mutually exclusive on a ``contents`` entry.
      This is what a PDF, an image, or a generated spreadsheet needs.
    """

    JSON = "json"
    TEXT = "text"
    BLOB = "blob"


# ---------- Tool result content ----------


class ToolContentKind(str, Enum):
    """What a tool's rendered payload becomes in the result's ``content`` array.

    Declared per binding rather than sniffed from the payload, for the same
    reason as :class:`ResourceEncoding`: a base64 ``str`` and a text body are
    indistinguishable by inspection, so guessing would silently change
    behaviour for a tool that already returns one.

    - ``TEXT``: the payload is rendered per the binding's ``OutputFormat`` into
      one text block, and mirrored in ``structuredContent``. The default, and
      what every JSON-shaped tool wants.
    - ``IMAGE`` / ``AUDIO``: the payload is the media itself — ``bytes``, or a
      ``str`` already in base64. ⚠ These carry **no** ``structuredContent``:
      binary is not JSON, and advertising an ``outputSchema`` over it would
      describe a shape that never arrives.
    - ``RESOURCE_LINK``: the payload names resources rather than containing
      them — one mapping with ``uri`` / ``name`` (plus optional ``description``
      / ``mimeType``), or a list of them. ``structuredContent`` is kept, since
      the links *are* JSON, so the model sees both projections.

    Embedded (``resource``) blocks have no kind here on purpose. Inlining
    contents means producing them, which is what ``resources/read`` already
    does — a tool that wants to hand over a resource returns a
    ``RESOURCE_LINK`` and lets the client decide whether to spend context on
    the body. :meth:`ToolContentBlock.embedded_resource` remains available for
    a caller building blocks by hand.
    """

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    RESOURCE_LINK = "resource_link"


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
    "ArgumentBinding",
    "CLIENT_CAPABILITIES_META_KEY",
    "CLIENT_INFO_META_KEY",
    "CacheScope",
    "IconTheme",
    "JSONRPC_VERSION",
    "JsonRpcErrorCode",
    "JsonRpcId",
    "MAX_COMPLETION_VALUES",
    "MODERN_PROTOCOL_VERSIONS",
    "OutputFormat",
    "PROGRESS_TOKEN_META_KEY",
    "PROTOCOL_VERSION_META_KEY",
    "RESERVED_POOL_SEEDS",
    "RESERVED_POST_FETCH_KEYS",
    "ResourceEncoding",
    "ResultType",
    "SERVER_INFO_META_KEY",
    "SESSIONLESS_METHODS",
    "ToolContentKind",
    "ToolKind",
    "UIPermission",
    "UIVisibility",
    "UI_EXTENSION_ID",
    "UI_META_KEY",
    "UI_RESOURCE_MIME_TYPE",
    "UnknownArguments",
]
