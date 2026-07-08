from __future__ import annotations

from typing import Any, cast

from django.http import HttpRequest
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from rest_framework_mcp.auth.types.token_info import TokenInfo


class DRFPermissionAdapter:
    """Bridge a DRF ``BasePermission`` class into the :class:`MCPPermission` Protocol.

    Sister-repo 0.12.0 added ``permission_classes`` on ``ServiceSpec`` and
    ``SelectorSpec`` — a sequence of DRF ``BasePermission`` *classes*. The MCP
    transport doesn't go through DRF views, so each class is wrapped in this
    adapter at registration time; the wrapped instance is constructed once
    (mirrors sister-repo's ``[perm() for perm in spec.permission_classes]``
    in its view ``get_permissions``).

    The DRF instance receives a synthesised :class:`rest_framework.request.Request`
    with ``user`` set to ``token.user`` and a lightweight view stand-in
    sufficient for the DRF permission contract (``request``, ``action``).
    The HTTP method on the underlying ``HttpRequest`` is left untouched
    (unlike :func:`~rest_framework_services.build_offline_context`, which forces
    ``POST`` for mutation-flow dispatch) — permission evaluation is method-agnostic.
    """

    def __init__(self, permission_class: type[BasePermission]) -> None:
        self._permission_class: type[BasePermission] = permission_class
        self._instance: BasePermission = permission_class()

    @property
    def permission_class(self) -> type[BasePermission]:
        return self._permission_class

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        drf_request: Request = _wrap_request(request, user=token.user)
        view: Any = _PermissionView(request=drf_request)
        # The DRF stub types the second arg as ``APIView``; ``_PermissionView``
        # is a structural stand-in (request / action / kwargs) sufficient for
        # stock and most custom permissions, but isn't an ``APIView`` subclass.
        # Cast to ``Any`` at the boundary keeps the rest of the package
        # statically typed.
        return bool(self._instance.has_permission(drf_request, view))  # ty: ignore[invalid-argument-type]

    def required_scopes(self) -> list[str]:
        # DRF permissions don't carry OAuth-scope semantics natively. Subclasses
        # of ``DRFPermissionAdapter`` (or sibling ``MCPPermission`` impls) are
        # the place to surface scope requirements.
        return []


class _PermissionView:
    """Minimal view stand-in for DRF permission evaluation.

    DRF permissions take ``has_permission(request, view)``. Most stock
    permissions only read ``view.action`` (set by ``ViewSet`` routing) — which
    doesn't exist outside a viewset, so we expose ``None``. Custom permissions
    that walk view attributes will see whatever they query as missing rather
    than crash, because ``__getattr__`` raises ``AttributeError`` cleanly.
    """

    def __init__(self, *, request: Request) -> None:
        self.request: Request = request
        self.action: str | None = None
        self.kwargs: dict[str, Any] = {}


def _wrap_request(http_request: HttpRequest, *, user: Any) -> Request:
    """Wrap an :class:`HttpRequest` as a DRF :class:`Request` with the supplied user.

    ``Request(http_request)`` is the canonical DRF upgrade path; we set
    ``.user`` explicitly so MCP-supplied auth state flows through without
    DRF re-running its own ``authenticators`` chain.
    """
    raw: Any = Request(http_request)  # ty: ignore[too-many-positional-arguments]
    drf_request: Request = cast(Request, raw)
    drf_request.user = user
    return drf_request


__all__ = ["DRFPermissionAdapter"]
