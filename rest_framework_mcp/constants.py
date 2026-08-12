"""The package's enums and shared constants.

The single file allowed to export several symbols (see ``CLAUDE.md``), so new
enums and shared constant-like values go here rather than into leaf modules.
The top-level ``rest_framework_mcp.__init__`` re-exports the public names.
"""

from __future__ import annotations

from enum import Enum, IntEnum

# ``dispatch_spec`` is the single dispatch core and owns these neutral-core
# policies, so MCP consumes them rather than keeping a parallel copy.
from rest_framework_services import ArgumentBinding, UnknownArguments
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS

"""Keys carrying transport-controlled pool seeds, owned by the sister repo.

A client-supplied argument with one of these names would override the
transport's authoritative values (a credential-spoofing footgun), so the spread
drops them. The dispatched callable may still *declare* a parameter of that
name; it receives the seed, which is the documented idiom.
"""

# ---------- JSON-RPC envelope ----------

JSONRPC_VERSION: str = "2.0"
"""The JSON-RPC protocol version this server speaks. MCP layers on JSON-RPC
2.0; every envelope carries ``"jsonrpc": "2.0"``."""

JsonRpcId = str | int | None
"""Type alias for the JSON-RPC ``id`` field. JSON-RPC 2.0 allows string,
integer or null IDs; notifications carry ``null``, requests carry a non-null ID
that clients correlate responses back by."""


class JsonRpcErrorCode(IntEnum):
    """JSON-RPC 2.0 standard error codes plus MCP-specific reservations.

    JSON-RPC defines -32700 through -32600 and -32603. MCP then partitions the
    server-error range: ``-32000..-32019`` is implementation-defined,
    ``-32020..-32099`` is reserved for the spec itself, and everything
    allocated here sits in the implementation-defined half — with one
    exception.

    **``-32002`` is not ours to allocate.** The resources spec names it for
    "Resource not found", and the ``2026-07-28`` revision singles it out as the
    one legacy code clients should keep recognising, so a spec-following client
    reads it as a missing resource whatever a server spends it on. It belongs
    to :attr:`RESOURCE_NOT_FOUND` and nothing else.

    ``-32003`` and ``-32004`` are **burned** rather than reused: they were this
    package's own not-found codes before the wire values were aligned with the
    spec, so an older client still reads them as "resource/prompt not found"
    and "unknown tool". The next implementation-defined code allocated is
    ``-32006``.
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
    # Spec-reserved (``-32020..-32099``), allocated by the MCP specification
    # itself. Not ours to number or to repurpose.
    HEADER_MISMATCH = -32020
    MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
    UNSUPPORTED_PROTOCOL_VERSION = -32022


# ---------- Output formatting ----------


class OutputFormat(str, Enum):
    """Encoding of a ``ToolResult``'s human-readable text block.

    Only ``content[0]`` varies; ``structuredContent`` is always JSON.

    Attributes:
        JSON: Pretty-printed JSON. The safe default.
        TOON: Token-oriented object notation, compact for large uniform arrays.
            Falls back to JSON when the optional ``toon`` extra is absent.
        AUTO: Per-payload choice — TOON for a uniform list of objects, JSON
            otherwise.
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
# ``ArgumentBinding`` and ``UnknownArguments`` are imported from drf-services at
# the top of this module. Service tools default to ``BUNDLE`` (one validated
# ``data`` payload) and selector tools to ``SPREAD_AUTHOR_WINS``.


# ---------- Tool kind discriminator ----------


class ToolKind(Enum):
    """Discriminator for :class:`ToolDefinition` and the
    :func:`register_tools` dispatch table.

    Internal-only — never appears on the wire. ``SERVICE`` maps to
    :meth:`MCPServer.register_service_tool`, ``SELECTOR`` to
    :meth:`MCPServer.register_selector_tool`. Prefer
    :meth:`ToolDefinition.service` / :meth:`ToolDefinition.selector` over
    passing this kwarg by hand — those are the typed entry points.
    """

    SERVICE = "service"
    SELECTOR = "selector"


# ---------- Reserved kwarg-pool keys (shared across handlers) ----------

RESERVED_POST_FETCH_KEYS: frozenset[str] = frozenset({"ordering", "page", "limit"})
"""Keys the selector-tool post-fetch pipeline consumes.

Stripped from the dispatched selector's kwarg pool, which would otherwise
receive kwargs it never declared. Scoped to that pool only: the ``FilterSet``
is handed the arguments unstripped, because a spec whose ``filter_set`` carries
an ``OrderingFilter`` advertises ``ordering`` through the reflected schema and
must therefore receive it.
"""


MODERN_PROTOCOL_VERSIONS: frozenset[str] = frozenset({"2026-07-28"})
"""Protocol revisions that carry version, identity and capabilities per request.

The spec's own split: **modern** revisions (``2026-07-28`` and later) declare
everything on each request and hold no session; **legacy** ones
(``2025-11-25`` and earlier) negotiate once through ``initialize``. Every
era branch in this package reads from this set, so adding a revision is one
edit. Both eras are served concurrently on one endpoint, because legacy clients
have no fall-forward mechanism.
"""

MCP_ERROR_HEADER: str = "MCP-Error"
"""Response header naming the *class* of a transport-level rejection.

Ours, not the spec's, which fixes the status code and says nothing about
diagnosis: a ``404`` from an unknown session and a ``404`` from a load balancer
with no matching rule are otherwise indistinguishable, and the JSON-RPC body
that would tell them apart is lost to clients that log
``${status} ${statusText}`` over HTTP/2, which has no reason phrase.

It carries strictly less than the body it summarises, so it leaks nothing new:
the session slugs deliberately do not separate "unknown id" from "id owned by
another principal", preserving the no-ownership-oracle property. Set on failure
responses only.
"""

SESSION_MISSING_HINT: str = "session-missing"
"""No ``Mcp-Session-Id`` header arrived (paired with ``400``)."""

SESSION_UNKNOWN_HINT: str = "session-unknown"
"""A session id arrived that this server will not honour — expired, evicted,
terminated, or minted for a different principal. Deliberately one slug for all
four (paired with ``404``); the client's remedy is the same in every case:
re-``initialize``."""

PROTOCOL_VERSION_META_KEY: str = "io.modelcontextprotocol/protocolVersion"
"""Per-request ``_meta`` key naming the revision a modern request speaks.

**This is the era discriminator** — not the ``MCP-Protocol-Version`` header,
which legacy clients have sent since ``2025-06-18``, and not the method, since
most methods exist in both eras.
"""

CLIENT_INFO_META_KEY: str = "io.modelcontextprotocol/clientInfo"
"""Per-request ``_meta`` key carrying the client's self-reported identity.
Optional and unverified; parsed for introspection and logging, and nothing
branches on it."""

CLIENT_CAPABILITIES_META_KEY: str = "io.modelcontextprotocol/clientCapabilities"
"""Per-request ``_meta`` key declaring what the client supports.

Required on a modern request, and empty (``{}``) is a valid declaration. It
replaces the ``initialize`` handshake's one-time exchange: a server **MUST
NOT** rely on a capability the client did not declare *on that request*.
"""

PROGRESS_TOKEN_META_KEY: str = "progressToken"
"""Per-request ``_meta`` key by which a client asks to be told about progress.

Unprefixed and identical in both eras, so progress needs no era branch. Its
presence is the *only* signal that streaming is wanted: a server MAY decline to
notify at all, so a request without the token is answered with a single JSON
object rather than a one-event stream.
"""

SESSIONLESS_METHODS: frozenset[str] = frozenset({"initialize", "server/discover"})
"""Methods answerable before a session exists.

The legacy transport requires an ``Mcp-Session-Id`` on everything except the
request that mints one. ``server/discover`` joins it because its purpose is to
be the first thing a client sends. Both are exempt from the
``MCP-Protocol-Version`` header requirement for the same reason, and only
``initialize`` mints a session.
"""

SERVER_INFO_META_KEY: str = "io.modelcontextprotocol/serverInfo"
"""Reserved ``_meta`` key carrying the server's self-reported identity.

Where ``server/discover`` puts what ``initialize`` returned as a top-level
``serverInfo``. Unverified, and clients are told not to make security decisions
from it — hence the metadata namespace rather than negotiated protocol state.
"""


# ---------- Result envelope ----------


class ResultType(str, Enum):
    """The discriminator every result carries from ``2026-07-28`` onward.

    A **MUST** for servers implementing that revision and harmless before it —
    a legacy result object is an open shape, and a client on an older revision
    reads an absent ``resultType`` as ``complete`` — so it is emitted
    unconditionally rather than era-branched.

    Attributes:
        COMPLETE: The result is the answer.
        INPUT_REQUIRED: The result asks the client for input and expects the
            original request to be retried with the answers. Nothing here
            produces one yet; the vocabulary is the spec's.
    """

    COMPLETE = "complete"
    INPUT_REQUIRED = "input_required"
    TASK = "task"
    """A durable handle instead of the result — see :mod:`rest_framework_mcp.tasks`.

    The spec types ``ResultType`` as ``"complete" | "input_required" | string``,
    the open tail being how extensions add their own. Servers **MUST NOT** set
    this on any result other than a ``CreateTaskResult``, which is why nothing
    stamps it centrally the way ``COMPLETE`` is stamped — the task handler sets
    it explicitly and no other handler can reach it.
    """


class CacheScope(str, Enum):
    """How widely a cacheable result may be reused, per ``Cache-Control``.

    **Derived, never configured.** ``PUBLIC`` licenses any intermediary to
    serve the response *across authorization contexts*, so a result that varies
    by caller and is labelled ``PUBLIC`` is a cross-tenant disclosure with a
    cache in front of it — precisely the mistake a settings knob would invite.
    The handlers work it out from what shaped the response: a
    permission-filtered listing is ``PRIVATE``, an unfiltered one ``PUBLIC``,
    and a resource body always ``PRIVATE``.
    """

    PUBLIC = "public"
    PRIVATE = "private"


# ---------- Argument completion ----------

MAX_COMPLETION_VALUES: int = 100
"""The spec's hard cap on ``values`` in one ``completion/complete`` result. A
completer may return more; the handler slices to this and sets ``hasMore``."""


# ---------- Display metadata ----------


class IconTheme(str, Enum):
    """Which background an :class:`~rest_framework_mcp.protocol.types.icon.Icon`
    was designed for.

    Omitting the theme (``None``) tells the client the icon works on either,
    which is right for most artwork. Declare it only when shipping a
    light/dark pair.
    """

    LIGHT = "light"
    DARK = "dark"


# ---------- Resource body encoding ----------


class ResourceEncoding(str, Enum):
    """How a resource's selector return value becomes the ``resources/read`` body.

    Declared separately from the binding's ``mimeType`` rather than sniffed
    from it — sniffing would silently change behaviour for anyone already
    advertising a non-JSON type.

    Attributes:
        JSON: Pretty-print the value as JSON. The default, and what every
            selector-backed data resource wants.
        TEXT: The value is already the body; the selector must return a
            ``str``. For HTML, Markdown, CSV or plain text, where
            JSON-encoding would wrap the payload in a quoted string literal.
        BLOB: The value is binary. The selector returns ``bytes`` and the body
            is base64-encoded into the spec's ``blob`` field instead of
            ``text`` — the two are mutually exclusive on a ``contents`` entry.
    """

    JSON = "json"
    TEXT = "text"
    BLOB = "blob"


# ---------- Tool result content ----------


class ToolContentKind(str, Enum):
    """What a tool's rendered payload becomes in the result's ``content`` array.

    Declared per binding rather than sniffed from the payload, for the same
    reason as :class:`ResourceEncoding`: a base64 ``str`` and a text body are
    indistinguishable by inspection.

    Embedded (``resource``) blocks have no kind here on purpose — inlining
    contents means producing them, which ``resources/read`` already does, so a
    tool returns a ``RESOURCE_LINK`` and lets the client decide whether to
    spend context on the body. :meth:`ToolContentBlock.embedded_resource`
    remains available for a caller building blocks by hand.

    Attributes:
        TEXT: One text block rendered per the binding's ``OutputFormat``,
            mirrored in ``structuredContent``. The default.
        IMAGE: The payload is the media itself — ``bytes``, or a ``str``
            already in base64. Carries **no** ``structuredContent``: binary is
            not JSON, and an ``outputSchema`` over it would describe a shape
            that never arrives.
        AUDIO: As ``IMAGE``.
        RESOURCE_LINK: The payload names resources rather than containing them
            — one mapping with ``uri`` / ``name`` (plus optional
            ``description`` / ``mimeType``), or a list of them.
            ``structuredContent`` is kept, since the links *are* JSON.
    """

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    RESOURCE_LINK = "resource_link"


# ---------- MCP Apps (interactive UI) ----------

UI_RESOURCE_MIME_TYPE: str = "text/html;profile=mcp-app"
"""Mime type identifying a resource as an interactive HTML view. Defined by the
MCP Apps extension; a host that does not implement it sees an ordinary HTML
resource."""

UI_META_KEY: str = "ui"
"""The ``_meta`` key MCP Apps owns on a resource or tool descriptor. ``_meta``
is a namespace shared by every extension, each owning one top-level key."""


class UIPermission(str, Enum):
    """A browser capability an interactive view asks the host to grant.

    The host decides; this only declares what the view would use, in the
    resource's ``_meta.ui.permissions``. Anything not declared is denied by the
    iframe sandbox the host builds.
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

    Attributes:
        MODEL: The agent may call it — ordinary tool behaviour.
        APP: The view may call it. An ``APP``-only tool is a fine-grained
            operation that serves the view rather than the conversation.
    """

    MODEL = "model"
    APP = "app"


# ---------- Subscriptions ----------


SUBSCRIPTION_ID_META_KEY: str = "io.modelcontextprotocol/subscriptionId"
"""``_meta`` key correlating a notification with the subscription that wanted it.

The value is the JSON-RPC id of the ``subscriptions/listen`` request that
opened the stream, carried on **every** notification it delivers and on the
result that closes it, so a client can run several subscriptions over one
connection and still tell them apart.
"""


class NotificationKind(str, Enum):
    """The notification types a client can opt in to, other than by URI or id.

    **Opt-in is a MUST**, not a courtesy: *"the server MUST NOT send
    notification types the client has not explicitly requested."* So this is a
    closed set, and anything outside the request's filter is not sent.

    The values are the notification methods with the ``notifications/`` prefix
    stripped, and the filter field names are their camelCase forms — kept
    mechanically related so a new kind cannot be added to one and forgotten in
    the other.
    """

    TOOLS_LIST_CHANGED = "tools/list_changed"
    PROMPTS_LIST_CHANGED = "prompts/list_changed"
    RESOURCES_LIST_CHANGED = "resources/list_changed"

    @property
    def method(self) -> str:
        return f"notifications/{self.value}"

    @property
    def filter_field(self) -> str:
        """The ``SubscriptionFilter`` key that opts in to this kind."""
        return _NOTIFICATION_FILTER_FIELDS[self]


_NOTIFICATION_FILTER_FIELDS: dict[NotificationKind, str] = {
    NotificationKind.TOOLS_LIST_CHANGED: "toolsListChanged",
    NotificationKind.PROMPTS_LIST_CHANGED: "promptsListChanged",
    NotificationKind.RESOURCES_LIST_CHANGED: "resourcesListChanged",
}

SUBSCRIPTIONS_LISTEN_METHOD: str = "subscriptions/listen"
"""The method that opens a notification stream. Named here rather than inline
at the transport because it is the one method the viewset branches on before
dispatch."""

TASK_STATUS_METHOD: str = "notifications/tasks"
"""Pushed when a task changes status, to whoever subscribed to that task.

Carries the **whole** task, not a delta — identical to what ``tasks/get`` would
have returned at that moment — so a missed notification costs nothing and
polling stays genuinely optional."""

RESOURCE_UPDATED_METHOD: str = "notifications/resources/updated"
"""Sent when a subscribed resource changed and may need re-reading."""

SUBSCRIPTIONS_ACKNOWLEDGED_METHOD: str = "notifications/subscriptions/acknowledged"
"""**MUST be the first message a subscription carries.** It reports the subset
of the requested filter the server agreed to honour, so a client learns
immediately that (say) ``promptsListChanged`` will never arrive because this
server has no prompts, rather than waiting indefinitely for it."""


# ---------- Tasks extension ----------


TASKS_EXTENSION_ID: str = "io.modelcontextprotocol/tasks"
"""Identifier for the tasks extension, in both directions.

Unlike :data:`UI_EXTENSION_ID` this one is symmetric: the client declares it
under ``clientCapabilities.extensions`` on **every** request and the server
under ``capabilities.extensions`` in ``server/discover``, with an empty settings
object each way. The client's declaration is per request, and a server **MUST
NOT** answer with a task "regardless of prior declarations".
"""

TASK_METHODS: frozenset[str] = frozenset({"tasks/get", "tasks/update", "tasks/cancel"})
"""The extension's three methods.

There is no ``tasks/list``, and the omission is the spec's: without sessions
there is no principal-scoped view to list *over*, so the only defence against
one caller reading another's work is that task ids are unguessable. Retrieval
is polling ``tasks/get``, paced by ``pollIntervalMs``.
"""


class TaskStatus(str, Enum):
    """Lifecycle of a task, per the extension.

    ``WORKING`` and ``INPUT_REQUIRED`` are live; the other three are terminal
    and a task never leaves them.

    **``FAILED`` is narrower than it looks.** The spec forbids using the status
    to represent non-JSON-RPC errors. A tool that raises ``ServiceError`` has
    *completed* — it produced a well-formed ``CallToolResult`` carrying
    ``isError: true``. ``FAILED`` is for the task machinery itself failing: the
    worker died, the payload could not be revived. Getting this backwards would
    hide every tool error behind a status the client reads as "the server
    broke".
    """

    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_TASK_STATUSES


_TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


class TaskPolicy(str, Enum):
    """Whether a binding may — or must — answer with a task handle.

    **A policy surface, not a wire field.** The shipped extension makes *"the
    server the sole decider; clients do not signal task preference on the
    request itself"*, so the decision has to be made on this side, and the
    binding is where every other per-tool knob lives.

    Attributes:
        FORBIDDEN: Never a task. The default, so every tool registered before
            this existed behaves exactly as it did.
        OPTIONAL: A task for a client that declared the extension, an ordinary
            inline call for one that did not. The safe choice for a slow tool
            that can still run inline.
        REQUIRED: A task, or nothing — a client that did not declare the
            extension gets ``-32021``. For work that genuinely cannot finish
            inside a request, where running it anyway would just hit the
            deadline.
    """

    FORBIDDEN = "forbidden"
    OPTIONAL = "optional"
    REQUIRED = "required"


# ---------- Elicitation / multi round-trip requests ----------


ELICITATION_CREATE_METHOD: str = "elicitation/create"
"""The one server-to-client request this package ever issues.

**It is not sent as a request.** From ``2026-07-28`` the server-initiated
direction is gone: an ``ElicitRequest`` travels as a *value* inside an
``InputRequiredResult``'s ``inputRequests`` map, and the client answers by
retrying the original call. The method name survives only as the discriminator
naming which kind of input is being asked for. The siblings the spec allows
there, ``sampling/createMessage`` and ``roots/list``, are Deprecated as of this
revision and are not built.
"""

ELICITATION_KEY: str = "additionalInput"
"""The key this package files its one question under in ``inputRequests``.
Keys are server-assigned and need only be unique within a single result; one
``AdditionalInputRequired`` asks one question, so a fixed name is enough."""

REQUEST_STATE_SALT: str = "rest_framework_mcp.elicitation.request_state"
"""Namespace for the HMAC over ``requestState``.

A salt rather than a bare ``SECRET_KEY`` signature so a token minted here can
never verify against another of the project's signed values (password reset
links, session cookies, other ``django.core.signing`` callers) or vice versa."""


class ElicitAction(str, Enum):
    """What the user did with the form, per ``ElicitResult.action``.

    The three are not interchangeable and this package does not collapse them:
    ``DECLINE`` is a decision, ``CANCEL`` is the absence of one. Both stop the
    call, but a client — or a model reading the error — can tell "the user said
    no" from "the user closed the dialog", and only the second is worth
    retrying.
    """

    ACCEPT = "accept"
    DECLINE = "decline"
    CANCEL = "cancel"


ELICITATION_SCALAR_TYPES: frozenset[str] = frozenset({"string", "number", "integer", "boolean"})
"""The JSON-Schema ``type`` values a form field may declare.

``requestedSchema`` is a **restricted** subset: *"Only top-level properties are
allowed, without nesting."* ``object`` is therefore never valid, and ``array``
only in the multi-select-enum shape — which is why that one is checked
separately rather than being a fourth member here."""


UI_EXTENSION_ID: str = "io.modelcontextprotocol/ui"
"""Identifier a client uses to advertise MCP Apps support.

Client to server only: it arrives under ``capabilities.extensions`` in the
``initialize`` request and the spec defines no matching server-side capability.
Parsed for introspection; nothing gates on it (see :class:`UIVisibility`).
"""


__all__ = [
    "ArgumentBinding",
    "CLIENT_CAPABILITIES_META_KEY",
    "CLIENT_INFO_META_KEY",
    "CacheScope",
    "ELICITATION_CREATE_METHOD",
    "ELICITATION_KEY",
    "ELICITATION_SCALAR_TYPES",
    "ElicitAction",
    "IconTheme",
    "JSONRPC_VERSION",
    "JsonRpcErrorCode",
    "JsonRpcId",
    "MAX_COMPLETION_VALUES",
    "MCP_ERROR_HEADER",
    "MODERN_PROTOCOL_VERSIONS",
    "OutputFormat",
    "PROGRESS_TOKEN_META_KEY",
    "PROTOCOL_VERSION_META_KEY",
    "REQUEST_STATE_SALT",
    "RESERVED_POOL_SEEDS",
    "RESERVED_POST_FETCH_KEYS",
    "ResourceEncoding",
    "ResultType",
    "SERVER_INFO_META_KEY",
    "SESSIONLESS_METHODS",
    "SESSION_MISSING_HINT",
    "SESSION_UNKNOWN_HINT",
    "NotificationKind",
    "RESOURCE_UPDATED_METHOD",
    "SUBSCRIPTIONS_ACKNOWLEDGED_METHOD",
    "SUBSCRIPTIONS_LISTEN_METHOD",
    "SUBSCRIPTION_ID_META_KEY",
    "TASKS_EXTENSION_ID",
    "TASK_STATUS_METHOD",
    "TASK_METHODS",
    "TaskPolicy",
    "TaskStatus",
    "ToolContentKind",
    "ToolKind",
    "UIPermission",
    "UIVisibility",
    "UI_EXTENSION_ID",
    "UI_META_KEY",
    "UI_RESOURCE_MIME_TYPE",
    "UnknownArguments",
]
