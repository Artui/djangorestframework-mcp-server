from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from rest_framework_services import UNSET, UnsetType
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.constants import ResourceEncoding
from rest_framework_mcp.protocol.types.icon import Icon

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class ResourceBinding(Generic[ResultT]):
    """All wiring for a single MCP resource (or resource template).

    A resource is a selector callable plus a URI template. The MCP server
    invokes the selector directly via ``resolve_callable_kwargs`` +
    ``run_selector`` — there is no view or viewset in the dispatch path.

    ``output_serializer`` is what ``resources/read`` renders the selector's
    return value through, and ``mime_type`` advertises the type that body will
    carry. ``kwargs_provider`` mirrors ``SelectorSpec.kwargs``: when set, the
    handler invokes it once per request and merges the returned dict into the
    kwarg pool, passing a synthesised
    :class:`~rest_framework_services.OfflineServiceView` whose ``view.kwargs``
    holds the URI-template variables and whose ``view.action`` is the binding
    name. ``annotations`` and ``meta`` are emitted verbatim on the listing
    entry, and ``meta`` also on the ``contents`` block of ``resources/read``.

    The generic parameter is purely informational, letting callers pin the
    selector's return type for type-checker help.
    """

    name: str
    uri_template: str
    description: str | None
    selector: Callable[..., ResultT]
    kind: SelectorKind
    """Pulled out of ``SelectorSpec.kind`` by the adapter, so the binding does
    not carry a reference to the whole spec. ``LIST`` invokes the output
    serializer with ``many=True``, ``RETRIEVE`` (the common case for
    URI-template resources) with ``many=False``. Resources have no post-fetch
    pipeline, so both kinds are accepted unconditionally."""

    output_serializer: type | None = None
    mime_type: str = "application/json"
    encoding: ResourceEncoding = ResourceEncoding.JSON
    """How the selector's value becomes the ``resources/read`` body. Declared
    rather than inferred from ``mime_type``, so advertising a new mime type
    never silently changes the encoding. See :class:`ResourceEncoding`."""

    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    # Free-form for the reason given on ``ToolBinding.meta``.
    meta: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    completions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    """Argument name to completer callable, powering ``completion/complete``.

    A completer is dispatched through ``resolve_callable_kwargs`` against a
    pool of ``value`` (the text typed so far), ``arguments`` (siblings the
    client has already resolved, also spread by name), ``request`` and
    ``user``. It returns an iterable of suggestions — a list, a generator or a
    queryset — which the handler slices to the spec's cap rather than
    draining."""

    cache_ttl_ms: int | UnsetType = UNSET
    """How long a client may cache this resource's body, in milliseconds.
    ``UNSET`` takes the server's ``RESOURCE_CACHE_TTL_MS``, ``0`` by default
    because a resource body is live data. Worth setting on anything genuinely
    static: hosts prefetch interactive views before any tool call, so a zero
    TTL means fetching the same HTML repeatedly."""

    icons: tuple[Icon, ...] = ()
    """Display icons, emitted in this resource's listing entry. Purely
    presentational; nothing in dispatch reads them."""

    # Loosely typed on purpose, so providers typed against the upstream
    # ``SelectorSpec.kwargs`` field (which uses generic ``ExtraT`` bounds) are
    # accepted without contravariance friction.
    kwargs_provider: Callable[..., Any] | None = None
    always_listed: bool = False
    """Keep this resource in ``resources/list`` (or
    ``resources/templates/list``) even when ``FILTER_LISTINGS_BY_PERMISSIONS``
    would drop it — same semantics as :attr:`ToolBinding.always_listed`."""

    @property
    def is_template(self) -> bool:
        return "{" in self.uri_template


__all__ = ["ResourceBinding"]
