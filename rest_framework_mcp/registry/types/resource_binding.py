from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

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

    ``output_serializer`` is consulted by ``resources/read`` to render the
    selector's return value. ``mime_type`` advertises the encoding we will
    return — usually ``"application/json"``.

    ``kwargs_provider`` mirrors ``SelectorSpec.kwargs`` from
    ``djangorestframework-services >= 0.6``: when set, the handler invokes it
    once per request and merges the returned dict into the kwarg pool. The
    provider receives a synthesised
    :class:`~rest_framework_services.OfflineServiceView` (URI-template
    variables exposed as ``view.kwargs``, the binding name as
    ``view.action``).

    The ``Generic[ResultT]`` parameter is purely informational — it lets
    callers pin the selector's return type for IDE / type-checker help.
    Defaults to ``Any`` when omitted.
    """

    name: str
    uri_template: str
    description: str | None
    selector: Callable[..., ResultT]
    kind: SelectorKind
    """Required, no default. Pulled out of ``SelectorSpec.kind`` by the adapter
    so the binding doesn't carry a reference to the whole spec. ``LIST`` invokes
    the output serializer with ``many=True``; ``RETRIEVE`` (the common case for
    URI-template resources) invokes it with ``many=False``. Resources have no
    post-fetch pipeline, so both kinds are unconditionally accepted."""

    output_serializer: type | None = None
    mime_type: str = "application/json"
    encoding: ResourceEncoding = ResourceEncoding.JSON
    """How the selector's value becomes the ``resources/read`` body. ``JSON``
    (the default) pretty-prints it; ``TEXT`` returns it verbatim, which is what
    an HTML / Markdown / CSV resource needs. Declared rather than inferred from
    ``mime_type``, so advertising a new mime type never silently changes how the
    body is encoded."""

    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    # See ``ToolBinding.meta`` — free-form ``_meta`` bundle for this
    # resource's ``resources/list`` (or ``resources/templates/list``) entry
    # and for the ``contents`` block ``resources/read`` returns.
    meta: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    completions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    """Argument name → completer callable, powering ``completion/complete``.

    A completer is dispatched through ``resolve_callable_kwargs`` against a
    pool of ``value`` (the text typed so far), ``arguments`` (siblings the
    client has already resolved, also spread by name), ``request`` and
    ``user``. It returns an iterable of suggestions — a list, a generator or
    a queryset — and the handler slices it to the spec's cap rather than
    draining it."""

    icons: tuple[Icon, ...] = ()
    """Display icons for this entry, emitted in its listing. Purely
    presentational — a client renders them; nothing in dispatch reads them."""

    # The signature is intentionally loose — ``Callable[..., Any]`` rather
    # than ``Callable[[ServiceView, Request], dict]`` — so providers typed
    # against the upstream ``SelectorSpec.kwargs`` field (which uses generic
    # ``ExtraT`` bounds) are accepted without contravariance friction.
    kwargs_provider: Callable[..., Any] | None = None
    always_listed: bool = False
    """Opt this resource back into listings it would otherwise be filtered out
    of. With ``FILTER_LISTINGS_BY_PERMISSIONS`` enabled, a resource is normally
    dropped from ``resources/list`` (and ``resources/templates/list`` for
    templates) if any binding permission denies the caller; ``True`` keeps it
    visible as a discovery aid. Same semantics as
    :attr:`ToolBinding.always_listed`."""

    @property
    def is_template(self) -> bool:
        return "{" in self.uri_template


__all__ = ["ResourceBinding"]
