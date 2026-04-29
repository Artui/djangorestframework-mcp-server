from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.output.format import OutputFormat

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

    @property
    def selector(self) -> Callable[..., ResultT]:
        if self.spec.selector is None:  # pragma: no cover - guarded at registration
            raise ValueError(f"SelectorToolBinding {self.name!r} has no selector")
        return self.spec.selector


__all__ = ["SelectorToolBinding"]
