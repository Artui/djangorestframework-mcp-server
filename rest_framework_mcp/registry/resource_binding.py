from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

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
    provider receives a synthesised :class:`MCPServiceView` (URI-template
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
    output_serializer: type | None = None
    mime_type: str = "application/json"
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    # The signature is intentionally loose — ``Callable[..., Any]`` rather
    # than ``Callable[[ServiceView, Request], dict]`` — so providers typed
    # against the upstream ``SelectorSpec.kwargs`` field (which uses generic
    # ``ExtraT`` bounds) are accepted without contravariance friction.
    kwargs_provider: Callable[..., Any] | None = None

    @property
    def is_template(self) -> bool:
        return "{" in self.uri_template


__all__ = ["ResourceBinding"]
