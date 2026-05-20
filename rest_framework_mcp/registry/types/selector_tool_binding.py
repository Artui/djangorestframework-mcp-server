from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments

ResultT = TypeVar("ResultT")
ExtraT = TypeVar("ExtraT", bound=dict[str, Any])


@dataclass(frozen=True)
class SelectorToolBinding(Generic[ResultT, ExtraT]):
    """All wiring for a single MCP **read-shaped** tool, derived from a ``SelectorSpec``.

    Mirrors :class:`ToolBinding` (which wraps a ``ServiceSpec`` for
    mutations), but the dispatch pipeline is read-shaped:

    .. code-block:: text

        arguments → validate(merged inputSchema) → run_selector
                  → FilterSet(data=...).qs    (if ``filter_set`` set)
                  → order_by(...)             (if ``ordering_fields`` set)
                  → paginate                  (if ``paginate=True``)
                  → output_serializer(many=True)
                  → ToolResult

    Selectors return raw, unscoped querysets — the tool layer owns the
    post-fetch pipeline. A binding with none of ``filter_set`` /
    ``ordering_fields`` / ``paginate`` set behaves like a plain RPC read
    that calls the selector and renders its return value verbatim.

    The ``Generic[InputT, ResultT, ExtraT]`` parameters mirror
    ``SelectorSpec``'s generics and are purely informational for type
    checkers.
    """

    name: str
    description: str | None
    spec: SelectorSpec[ResultT, ExtraT]
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
    # ``filter_set`` is a django-filter ``FilterSet`` class (or ``None`` to
    # skip filtering). Typed as ``Any`` because ``django-filter`` is an
    # optional dep behind the ``[filter]`` extra — narrowing the type would
    # force a hard import in this module.
    filter_set: Any | None = None
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
    # ``MERGE`` for selector tools: selectors typically declare their
    # query parameters as individual function arguments
    # (``def list_drafts(*, project_id, page=1, limit=10)``), so the
    # MCP layer spreads the validated/raw arguments across the pool.
    argument_binding: ArgumentBinding = ArgumentBinding.MERGE
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

    @property
    def selector(self) -> Callable[..., ResultT]:
        if self.spec.selector is None:  # pragma: no cover - guarded at registration
            raise ValueError(f"SelectorToolBinding {self.name!r} has no selector")
        return self.spec.selector


__all__ = ["SelectorToolBinding"]
