from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import UNSET, UnsetType, spec_to_json_schema
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

    The read-shaped mirror of [`ToolBinding`][rest_framework_mcp.registry.types.tool_binding.ToolBinding]. Selectors return raw,
    unscoped data and the tool layer owns every shape decision, chosen by
    ``kind``.

    ``kind=LIST`` runs the full pipeline:

    .. code-block:: text

        arguments → validate(merged inputSchema) → run_selector
                  → FilterSet(data=...).qs    (if ``filter_set`` set)
                  → order_by(...)             (if ``ordering_fields`` set)
                  → paginate                  (if ``paginate=True``)
                  → output_serializer(many=True)
                  → ToolResult

    With none of the three set it behaves as a plain RPC read, rendering the
    selector's return value verbatim.

    ``kind=RETRIEVE`` skips ordering and pagination but still applies queryset
    shaping and ``spec.filter_set`` before materializing the instance via
    ``.first()`` — so a "stats from a filtered set" retrieve works — then
    renders ``output_serializer(many=False)``. Pairing it with
    ``ordering_fields`` / ``paginate`` is rejected at construction: those knobs
    only mean something on a collection.

    ``paginate=True`` generates ``page`` / ``limit`` arguments, slices the
    queryset and wraps the response with ``items`` / ``page`` / ``totalPages``
    / ``hasNext``. ``ordering_fields`` is **deprecated**: it exposes each name
    as ``"<name>"`` and ``"-<name>"`` and hands raw ORM paths to
    ``.order_by()``, a second vocabulary for the same ``ordering`` argument a
    ``FilterSet``'s ``OrderingFilter`` already advertises. Declaring both is
    refused; declaring it alone still works for a spec with no ``filter_set``.

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
    # via the property below, like ``kind`` and ``selector``.
    ordering_fields: tuple[str, ...] = ()
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
    """Keep this binding in ``tools/list`` even when
    ``FILTER_LISTINGS_BY_PERMISSIONS`` would drop it — same semantics as
    [`ToolBinding.always_listed`][rest_framework_mcp.registry.types.tool_binding.ToolBinding.always_listed]."""

    query_params: tuple[QueryParam, ...] = ()
    """Read-shaping params routed to ``request.query_params`` at dispatch.

    Popped from the caller's arguments like a URL kwarg, but landing in the
    synthetic request's ``GET`` rather than ``view.kwargs`` — the channel a
    serializer reads when it branches on the query string. A ``filter_set``
    field is **not** one of these."""

    url_kwargs: tuple[UrlKwarg, ...] = ()
    """URL-derived values the model supplies as tool args, seeded into the
    off-HTTP view's ``kwargs`` instead of reaching the selector as ordinary
    params. Advertised in the ``inputSchema``, exempt from the unknown-argument
    check, and stripped from the dispatched params. See [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg]."""

    content_kind: ToolContentKind = ToolContentKind.TEXT
    """What this tool's payload becomes in the result's ``content`` array.
    ``TEXT`` renders JSON per ``output_format``; the other kinds project it
    into an image / audio / resource-link block. See [`ToolContentKind`][rest_framework_mcp.constants.ToolContentKind]."""

    content_mime_type: str | None = None
    """The media type for an ``IMAGE`` / ``AUDIO`` ``content_kind``.
    Required for those and meaningless for the rest — a resource link carries
    its own ``mimeType`` per entry."""

    task_policy: TaskPolicy = TaskPolicy.FORBIDDEN
    """Whether calling this tool hands back a task handle instead of a result.
    The choice lives on the binding because the extension makes the *server*
    the sole decider and gives the client no way to ask. See
    [`TaskPolicy`][rest_framework_mcp.constants.TaskPolicy]."""

    def __post_init__(self) -> None:
        if self.include_output_schema is True and self.include_structured_content is False:
            raise ImproperlyConfigured(
                f"Selector tool {self.name!r}: include_output_schema=True is "
                "incompatible with include_structured_content=False. The MCP spec "
                "requires that any tool advertising outputSchema also return "
                "conforming structuredContent. Set one of them differently."
            )
        if self.kind is SelectorKind.RETRIEVE:
            # ``filter_set`` is allowed on RETRIEVE — the dispatcher shapes and
            # filters the queryset before ``.first()``. Ordering and pagination
            # still only make sense on a collection.
            list_only: list[str] = []
            if self.ordering_fields:
                list_only.append("ordering_fields")
            if self.paginate:
                list_only.append("paginate")
            if list_only:
                raise ImproperlyConfigured(
                    f"Selector tool {self.name!r}: spec.kind=RETRIEVE is incompatible "
                    f"with list-shaped pipeline knob(s) {sorted(list_only)!r}. A "
                    "retrieve selector returns a single instance — there is no "
                    "collection to order or paginate. Either drop the knob(s) or "
                    "set the spec's kind to LIST."
                )
        if self.ordering_fields and self.filter_advertises_ordering:
            raise ImproperlyConfigured(
                f"Selector tool {self.name!r}: ordering is declared twice — "
                "spec.filter_set already advertises an 'ordering' argument, and "
                f"ordering_fields={list(self.ordering_fields)!r} would overwrite it "
                "under the same name with a different vocabulary (raw ORM paths "
                "rather than the FilterSet's public choices). Drop ordering_fields; "
                "the FilterSet's OrderingFilter is the canonical declaration."
            )
        if self.ordering_fields:
            warnings.warn(
                f"Selector tool {self.name!r}: ordering_fields is deprecated. Declare "
                "ordering with an OrderingFilter on the spec's filter_set — it is "
                "reflected into the inputSchema and applied by the FilterSet, so one "
                "vocabulary serves both the HTTP and MCP transports.",
                DeprecationWarning,
                stacklevel=2,
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

    @property
    def filter_advertises_ordering(self) -> bool:
        """Whether the spec's reflected shape already offers an ``ordering`` arg.

        ``django_filters.OrderingFilter`` subclasses ``ChoiceFilter``, so
        reflection maps it to an enum like any other choice filter: a spec
        carrying one advertises ``ordering`` with nothing declared here.

        Asked of the **reflected schema** rather than by isinstance-checking
        ``django_filters`` types, because that extra is optional and because
        the invariant worth encoding is that whatever the ``inputSchema``
        advertises, the dispatch delivers. Reading the same reflection the
        schema builder reads makes promise and delivery agree by construction.
        """
        reflected: dict[str, Any] = spec_to_json_schema(self.spec, phase="input") or {}
        return "ordering" in reflected.get("properties", {})


__all__ = ["SelectorToolBinding"]
