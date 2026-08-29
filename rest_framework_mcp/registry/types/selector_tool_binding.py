from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Generic, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import (
    UNSET,
    AudienceProjection,
    FieldMarking,
    UnsetType,
    build_audience_projection,
)
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.constants import (
    ArgumentBinding,
    OutputFormat,
    TaskPolicy,
    ToolContentKind,
    UnknownArguments,
)
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg
from rest_framework_mcp.registry.types.utils import validate_content_kind

ResultT = TypeVar("ResultT")
ExtraT = TypeVar("ExtraT", bound=dict[str, Any])


@dataclass(frozen=True)
class SelectorToolBinding(Generic[ResultT, ExtraT]):
    """All wiring for a single MCP **read-shaped** tool, from a ``SelectorSpec``.

    The read-shaped mirror of
    [`ToolBinding`][rest_framework_mcp.registry.types.tool_binding.ToolBinding].
    Selectors return raw, unscoped data and the tool layer owns every shape decision,
    chosen by ``kind``.

    ``kind=LIST`` runs the full pipeline:

    ```text
    arguments → validate(merged inputSchema) → run_selector
              → FilterSet(data=...).qs    (if ``filter_set`` set, and it
                                           orders too when it declares an
                                           ``OrderingFilter``)
              → paginate                  (if ``paginate=True``)
              → output_serializer(many=True)
              → ToolResult
    ```

    With neither set it behaves as a plain RPC read, rendering the selector's
    return value verbatim.

    ``kind=RETRIEVE`` skips pagination but still applies queryset shaping and
    ``spec.filter_set`` before materializing the instance via ``.first()`` — so
    a "stats from a filtered set" retrieve works — then renders
    ``output_serializer(many=False)``. Pairing it with ``paginate`` is rejected
    at construction: that knob only means something on a collection.

    ``paginate=True`` generates ``page`` / ``limit`` arguments, slices the
    queryset and wraps the response with ``items`` / ``page`` / ``totalPages``
    / ``hasNext``. Ordering has no binding-level knob at all: it is declared as
    an ``OrderingFilter`` on the spec's ``FilterSet``, reflected into the
    ``inputSchema`` and applied by the filter, so one vocabulary serves the
    HTTP transport and every agent transport alike.

    ``annotations`` and ``meta`` are emitted verbatim on this tool's
    ``tools/list`` entry, under ``annotations`` and ``_meta`` respectively.

    The generic parameters mirror ``SelectorSpec``'s and are purely
    informational for type checkers.
    """

    name: str
    description: str | None
    spec: SelectorSpec[ResultT, ExtraT]
    display_name: str | None = None
    """Consumer-only label, **never emitted on the MCP wire**, so a downstream
    library can render a richer label than the protocol ``title``."""

    display_description: str | None = None
    """Consumer-only blurb, the sibling of ``display_name`` and likewise
    never emitted on the MCP wire."""

    input_serializer: type | None = None
    """Custom non-filter tool arguments, declared MCP-side.

    ``SelectorSpec`` carries no input serializer of its own: a selector only
    describes how to fetch, and the HTTP transport validates the URL and query
    separately. MCP has no such split — every tool call is one ``arguments``
    dict — so arguments that are not filter / ordering / pagination knobs are
    declared here."""
    output_format: OutputFormat = OutputFormat.JSON
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    # Free-form for the reason given on ``ToolBinding.meta``.
    meta: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    icons: tuple[Icon, ...] = ()
    """Display icons, emitted in this tool's listing entry. Purely
    presentational; nothing in dispatch reads them."""

    include_structured_content: bool | None = None
    """Whether this tool's ``tools/call`` response carries
    ``structuredContent``. ``None`` defers to the
    ``INCLUDE_STRUCTURED_CONTENT`` setting."""

    include_output_schema: bool | None = None
    """Whether this tool's ``tools/list`` entry carries an ``outputSchema``.
    ``None`` defers to the ``INCLUDE_OUTPUT_SCHEMA`` setting.

    The MCP spec forbids advertising ``outputSchema`` while suppressing
    ``structuredContent``, so ``True`` together with
    ``include_structured_content=False`` is rejected at construction."""

    max_result_bytes: int | None | UnsetType = UNSET
    """Per-tool outbound result ceiling. ``UNSET`` defers to the server's
    ``MAX_RESULT_BYTES``, ``None`` disables it here, an ``int`` sets its own."""

    dispatch_timeout: float | None | UnsetType = UNSET
    """Per-tool dispatch deadline, in seconds. ``UNSET`` defers to the server's
    ``DISPATCH_TIMEOUT``, ``None`` disables it here. Async transport only."""

    max_page_size: int | None | UnsetType = UNSET
    """Per-tool ceiling on the model-supplied ``limit``. ``UNSET`` defers to the
    server's ``MAX_PAGE_SIZE``, ``None`` serves any ``limit`` the model asks
    for.

    Only meaningful with ``paginate=True``: an unpaginated selector has no
    ``limit`` to clamp, and clamping its result would drop rows with nothing in
    the payload to say so (see ``UnboundedListWarning``)."""
    # ----- read-shaped pipeline knobs -----
    # ``filter_set`` is not stored here; it is sourced from ``spec.filter_set``
    # via the property below, like ``kind`` and ``selector``. Ordering rides on
    # it as an ``OrderingFilter``, which is why pagination is the only knob left.
    paginate: bool = False
    argument_binding: ArgumentBinding = ArgumentBinding.SPREAD_AUTHOR_WINS
    """How MCP ``arguments`` flow into the kwarg pool. ``SPREAD_AUTHOR_WINS``
    for selector tools, because a selector typically declares its query
    parameters as individual function arguments
    (``def list_drafts(*, project_id, page=1, limit=10)``)."""

    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    """How unknown ``arguments`` keys are handled relative to the merged
    ``inputSchema`` (``input_serializer`` fields, ``filter_set`` properties,
    ordering, pagination). ``REJECT`` answers ``-32602``, ``PASSTHROUGH``
    merges them into the validated payload, ``IGNORE`` drops them."""

    always_listed: bool = False
    """Keep this binding in ``tools/list`` even when ``FILTER_LISTINGS_BY_PERMISSIONS``
    would drop it — same semantics as
    [`ToolBinding.always_listed`][rest_framework_mcp.registry.types.tool_binding.ToolBinding.always_listed]."""

    query_params: tuple[QueryParam, ...] = ()
    """Read-shaping params routed to ``request.query_params`` at dispatch.

    Popped from the caller's arguments like a URL kwarg, but landing in the
    synthetic request's ``GET`` rather than ``view.kwargs`` — the channel a
    serializer reads when it branches on the query string. A ``filter_set``
    field is **not** one of these."""

    url_kwargs: tuple[UrlKwarg, ...] = ()
    """URL-derived values the model supplies as tool args, seeded into the off-HTTP
    view's ``kwargs`` instead of reaching the selector as ordinary params. Advertised in
    the ``inputSchema``, exempt from the unknown-argument check, and stripped from the
    dispatched params. See
    [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg]."""

    content_kind: ToolContentKind = ToolContentKind.TEXT
    """What this tool's payload becomes in the result's ``content`` array. ``TEXT``
    renders JSON per ``output_format``; the other kinds project it into an image / audio
    / resource-link block. See
    [`ToolContentKind`][rest_framework_mcp.constants.ToolContentKind]."""

    content_mime_type: str | None = None
    """The media type for an ``IMAGE`` / ``AUDIO`` ``content_kind``.
    Required for those and meaningless for the rest — a resource link carries
    its own ``mimeType`` per entry."""

    task_policy: TaskPolicy = TaskPolicy.FORBIDDEN
    """Whether calling this tool hands back a task handle instead of a result.
    The choice lives on the binding because the extension makes the *server*
    the sole decider and gives the client no way to ask. See
    [`TaskPolicy`][rest_framework_mcp.constants.TaskPolicy]."""

    field_audiences: Mapping[str, FieldMarking] | None = None
    """Per-tool overrides layered over the ``FieldMarking`` declarations the
    output serializer carries on its own fields.

    The serializer stays authoritative — it is the one declaration the REST API,
    this transport, and an in-process toolset all read. This exists for the case
    one tool genuinely needs what a sibling hides: a lookup tool returning the
    identifier its neighbour drops.

    Declared on the registry entry's
    [`OfflineContract`][rest_framework_services.types.offline_contract.OfflineContract]
    and resolved here, so the field set an agent sees does not depend on which
    agent transport served it."""

    @property
    def output_serializer(self) -> type | None:
        """The serializer whose rendered output reaches the caller, if any."""
        return self.spec.output_serializer

    @cached_property
    def audience_projection(self) -> AudienceProjection:
        """This tool's resolved audience markings, derived once per binding.

        Drives both the projected payload and the advertised ``outputSchema``,
        so the two cannot disagree about which fields a caller will receive."""
        return build_audience_projection(
            self.output_serializer,
            overrides=self.field_audiences,
            name=f"Tool {self.name!r}",
        )

    def __post_init__(self) -> None:
        if self.include_output_schema is True and self.include_structured_content is False:
            raise ImproperlyConfigured(
                f"Selector tool {self.name!r}: include_output_schema=True is "
                "incompatible with include_structured_content=False. The MCP spec "
                "requires that any tool advertising outputSchema also return "
                "conforming structuredContent. Set one of them differently."
            )
        if self.kind is SelectorKind.RETRIEVE and self.paginate:
            # ``filter_set`` is allowed on RETRIEVE — the dispatcher shapes and
            # filters the queryset before ``.first()``, and an ``OrderingFilter``
            # on it is meaningful there too (which row ``.first()`` picks).
            # Paging a single instance is the one thing that never is.
            raise ImproperlyConfigured(
                f"Selector tool {self.name!r}: spec.kind=RETRIEVE is incompatible "
                "with the list-shaped pipeline knob 'paginate'. A retrieve selector "
                "returns a single instance — there is no collection to paginate. "
                "Either drop paginate or set the spec's kind to LIST."
            )
        validate_content_kind(
            name=self.name,
            content_kind=self.content_kind,
            content_mime_type=self.content_mime_type,
            include_structured_content=self.include_structured_content,
            include_output_schema=self.include_output_schema,
        )

    @property
    def kind(self) -> SelectorKind:
        """Shape discriminator, read from the spec's required ``kind`` field.

        Not stored independently on the binding: a second copy would only be a
        chance for the two to drift.
        """
        return self.spec.kind

    @property
    def selector(self) -> Callable[..., ResultT]:
        if self.spec.selector is None:  # pragma: no cover - guarded at registration
            raise ValueError(f"SelectorToolBinding {self.name!r} has no selector")
        return self.spec.selector

    @property
    def filter_set(self) -> Any | None:
        """Transport-neutral filtering, read from ``SelectorSpec.filter_set``.

        Delegated rather than copied, like ``kind`` and ``selector``,
        so a project declares its filterable shape once on the spec and both
        the HTTP and MCP transports honour it.

        Typed ``Any`` because ``django-filter`` is optional behind the
        ``[filter]`` extra, and narrowing would force a hard import here.
        """
        return self.spec.filter_set


__all__ = ["SelectorToolBinding"]
