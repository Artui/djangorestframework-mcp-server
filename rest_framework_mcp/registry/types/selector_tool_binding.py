from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments

ResultT = TypeVar("ResultT")
ExtraT = TypeVar("ExtraT", bound=dict[str, Any])


@dataclass(frozen=True)
class SelectorToolBinding(Generic[ResultT, ExtraT]):
    """All wiring for a single MCP **read-shaped** tool, derived from a ``SelectorSpec``.

    Mirrors :class:`ToolBinding` (which wraps a ``ServiceSpec`` for
    mutations), but the dispatch pipeline is read-shaped. The shape is
    chosen by :attr:`kind`:

    ``kind=LIST`` runs the full pipeline:

    .. code-block:: text

        arguments → validate(merged inputSchema) → run_selector
                  → FilterSet(data=...).qs    (if ``filter_set`` set)
                  → order_by(...)             (if ``ordering_fields`` set)
                  → paginate                  (if ``paginate=True``)
                  → output_serializer(many=True)
                  → ToolResult

    ``kind=RETRIEVE`` skips ordering / pagination but still applies
    queryset shaping + ``spec.filter_set`` before materializing the
    single instance via ``.first()`` (so a "stats from a filtered set"
    retrieve works, matching the sister repo's ``dispatch_spec``), then
    renders ``output_serializer(many=False)``. Combining ``RETRIEVE``
    with ``ordering_fields`` / ``paginate`` is rejected at construction
    (those knobs only make sense on a collection).

    Selectors return raw, unscoped data (a queryset for ``LIST``, a
    single instance for ``RETRIEVE``) — the tool layer owns shape
    decisions. A ``LIST`` binding with none of ``filter_set`` /
    ``ordering_fields`` / ``paginate`` set behaves like a plain RPC
    read that calls the selector and renders its return value verbatim.

    The ``Generic[InputT, ResultT, ExtraT]`` parameters mirror
    ``SelectorSpec``'s generics and are purely informational for type
    checkers.
    """

    name: str
    description: str | None
    spec: SelectorSpec[ResultT, ExtraT]
    # Consumer-only display metadata — never emitted on the MCP wire
    # (``tools/list`` ignores them). Provided so a downstream library can
    # render a richer label / blurb than the protocol ``title`` /
    # ``description``. ``None`` means "unset".
    display_name: str | None = None
    display_description: str | None = None
    # ``SelectorSpec`` from the sister repo doesn't carry an input
    # serializer (selectors only describe how to fetch; the HTTP transport
    # validates the URL/query separately). For MCP, where every tool call
    # carries a JSON ``arguments`` dict, custom non-filter args live here.
    input_serializer: type | None = None
    output_format: OutputFormat = OutputFormat.JSON
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    # Tri-state override for whether this tool's ``tools/call`` response
    # includes a ``structuredContent`` field. ``None`` (the default) defers
    # to the ``INCLUDE_STRUCTURED_CONTENT`` setting; ``True`` / ``False``
    # force the behavior regardless of the global.
    include_structured_content: bool | None = None
    # Tri-state override for whether this tool's ``tools/list`` entry
    # carries an ``outputSchema``. ``None`` (the default) defers to the
    # ``INCLUDE_OUTPUT_SCHEMA`` setting; ``True`` / ``False`` force the
    # behavior regardless of the global. The MCP spec forbids advertising
    # ``outputSchema`` while suppressing ``structuredContent``, so the
    # combination ``include_output_schema=True`` with
    # ``include_structured_content=False`` is rejected at construction time.
    include_output_schema: bool | None = None
    # ----- read-shaped pipeline knobs -----
    # ``filter_set`` is no longer stored here — it is sourced from
    # ``spec.filter_set`` via the property below (the spec is the single
    # source of truth, exactly like ``kind`` / ``selector``).
    # Field names allowed in the generated ``ordering`` enum. Each name is
    # exposed as both ``"<name>"`` (asc) and ``"-<name>"`` (desc) — django's
    # convention. Empty tuple disables ordering.
    ordering_fields: tuple[str, ...] = ()
    # When True, the binding generates ``page`` / ``limit`` arguments,
    # slices the queryset accordingly, and wraps the response with
    # pagination metadata (``items`` / ``page`` / ``totalPages`` /
    # ``hasNext``). When False, the queryset is rendered as-is.
    paginate: bool = False
    # How MCP ``arguments`` flow into the kwarg pool. Defaults to
    # ``SPREAD_AUTHOR_WINS`` for selector tools: selectors typically declare their
    # query parameters as individual function arguments
    # (``def list_drafts(*, project_id, page=1, limit=10)``), so the
    # MCP layer spreads the validated/raw arguments across the pool.
    argument_binding: ArgumentBinding = ArgumentBinding.SPREAD_AUTHOR_WINS
    # How unknown ``arguments`` keys are handled relative to the binding's
    # merged ``inputSchema`` (input_serializer fields + filter_set
    # properties + ordering + pagination). ``REJECT`` (default) rejects
    # unknown keys with ``-32602``; ``PASSTHROUGH`` merges them into the
    # validated payload; ``IGNORE`` silently drops them.
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    # See ``ToolBinding.always_listed`` — same opt-back-in semantics for
    # selector tools when ``FILTER_LISTINGS_BY_PERMISSIONS`` is enabled.
    always_listed: bool = False

    def __post_init__(self) -> None:
        if self.include_output_schema is True and self.include_structured_content is False:
            raise ImproperlyConfigured(
                f"Selector tool {self.name!r}: include_output_schema=True is "
                "incompatible with include_structured_content=False. The MCP spec "
                "requires that any tool advertising outputSchema also return "
                "conforming structuredContent. Set one of them differently."
            )
        if self.kind is SelectorKind.RETRIEVE:
            # ``filter_set`` is *allowed* on RETRIEVE — the dispatcher shapes +
            # filters the queryset before ``.first()`` (sister-repo parity).
            # Ordering / pagination still only make sense on a collection.
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

    @property
    def kind(self) -> SelectorKind:
        """Shape discriminator — derived from the spec's required ``kind`` field.

        Sister-repo 0.13+ made ``kind`` a required field on
        :class:`SelectorSpec`, so the binding doesn't store an
        independent copy — it would only be a chance for the two to
        drift. Exposed as a property so the dispatch layer can keep
        reading ``binding.kind`` unchanged.
        """
        return self.spec.kind

    @property
    def selector(self) -> Callable[..., ResultT]:
        if self.spec.selector is None:  # pragma: no cover - guarded at registration
            raise ValueError(f"SelectorToolBinding {self.name!r} has no selector")
        return self.spec.selector

    @property
    def filter_set(self) -> Any | None:
        """Transport-neutral filtering, sourced from the spec.

        Like :attr:`kind` and :attr:`selector`, this delegates to the
        :class:`SelectorSpec` rather than storing a copy — the spec is
        the single source of truth (``SelectorSpec.filter_set``,
        ``djangorestframework-services`` 0.18+). The MCP read pipeline
        and ``inputSchema`` generation read ``binding.filter_set``
        unchanged, so a project declares its filterable shape once, on
        the spec, and both the HTTP and MCP transports honour it.

        Typed ``Any`` because ``django-filter`` is an optional dep behind
        the ``[filter]`` extra — narrowing the type would force a hard
        import here.
        """
        return self.spec.filter_set


__all__ = ["SelectorToolBinding"]
