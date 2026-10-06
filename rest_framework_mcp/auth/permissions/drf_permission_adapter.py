from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from typing import Any, cast

from django.http import HttpRequest
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from rest_framework_mcp.auth.types.token_info import TokenInfo


class DRFPermissionAdapter:
    """Bridge a DRF ``BasePermission`` class into the
    [`MCPPermission`][rest_framework_mcp.auth.permissions.types.mcp_permission.MCPPermission]
    Protocol.

    ``ServiceSpec`` / ``SelectorSpec`` carry ``permission_classes`` as DRF
    ``BasePermission`` *classes*, and the MCP transport doesn't go through DRF
    views, so each class is wrapped here at registration time and instantiated
    once — mirroring what a DRF view's ``get_permissions`` does.

    The DRF instance receives a synthesised ``rest_framework.request.Request``
    with ``user`` set to ``token.user`` and a lightweight view stand-in
    sufficient for the DRF permission contract (``request``, ``action``,
    ``kwargs``). The
    HTTP method on the underlying ``HttpRequest`` is left untouched — unlike
    ``build_offline_context``, which forces
    ``POST`` for mutation dispatch — because permission evaluation is
    method-agnostic.

    **``auth`` is set alongside ``user``, and has to be.** DRF resolves
    ``request.auth`` lazily: reading it on a request that has never
    authenticated runs the (here empty) authenticator chain, which ends in
    ``_not_authenticated()`` and *overwrites* ``request.user`` with
    ``UNAUTHENTICATED_USER`` — on the wrapper and on the ``HttpRequest``
    underneath it. A permission class as ordinary as
    ``TokenHasScope`` reads ``request.auth`` first and every ``request.user``
    read after it would see ``AnonymousUser``, denying a properly scoped caller
    with nothing in the response explaining why. Assigning the backend's opaque
    payload — DRF's own convention for what ``auth`` holds — means the getter
    never reaches for the chain.

    **``view.kwargs`` is the route the call names.** A permission scoping by a
    route capture reads ``view.kwargs["project_pk"]``, as it would over HTTP.
    The adapter is built once at registration, where no call has named a route
    yet, so its own view carries ``{}``; a ``tools/call`` hands the URL kwargs it
    delivered through
    [`bind_view_kwargs`][rest_framework_mcp.auth.permissions.drf_permission_adapter.DRFPermissionAdapter.bind_view_kwargs]
    before the check, the same values its dispatch puts in ``view.kwargs``.
    """

    def __init__(self, permission_class: type[BasePermission]) -> None:
        self._permission_class: type[BasePermission] = permission_class
        self._instance: BasePermission = permission_class()
        # Empty until ``bind_view_kwargs`` hands a copy a call's route; a check
        # made with the registered adapter itself judges ``{}``
        # (``test_an_unbound_adapter_judges_an_empty_route``).
        self._view_kwargs: Mapping[str, Any] = {}

    @property
    def permission_class(self) -> type[BasePermission]:
        return self._permission_class

    @classmethod
    def bind_view_kwargs(
        cls, permissions: Iterable[Any], view_kwargs: Mapping[str, Any]
    ) -> tuple[Any, ...]:
        """``permissions`` as one call judges them, against the route it names.

        Every adapter among them is replaced by a copy whose stand-in view
        carries ``view_kwargs``; any other permission passes through as it is,
        since an ``MCPPermission`` judges the request and token and has no view.
        Copies rather than the registered adapters, which every concurrent call
        to the tool shares: a route written onto one would be judged on another
        caller's call (``test_binding_leaves_the_registered_adapter_unbound``).
        The wrapped DRF instance is shared by the copies, as it is by every call
        already.
        """
        return tuple(
            perm._bound_to(view_kwargs) if isinstance(perm, cls) else perm for perm in permissions
        )

    def _bound_to(self, view_kwargs: Mapping[str, Any]) -> DRFPermissionAdapter:
        # ``copy.copy`` rather than the constructor, so a subclass keeps what
        # its own ``__init__`` set and the permission is not instantiated again.
        bound: DRFPermissionAdapter = copy.copy(self)
        bound._view_kwargs = dict(view_kwargs)
        return bound

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        drf_request: Request = _wrap_request(request, user=token.user, auth=token.raw)
        view: Any = _PermissionView(request=drf_request, kwargs=self._view_kwargs)
        # The DRF stub types the second argument as ``APIView``; the stand-in is
        # structural, so it is typed ``Any`` at this one boundary to keep the
        # rest of the package statically typed.
        return bool(self._instance.has_permission(drf_request, view))

    def required_scopes(self) -> list[str]:
        # DRF permissions carry no OAuth-scope semantics; a subclass or a
        # sibling ``MCPPermission`` is where scope requirements surface.
        return []


class _PermissionView:
    """Minimal view stand-in for DRF permission evaluation.

    DRF permissions take ``has_permission(request, view)``, and most stock ones
    only read ``view.action`` — which has no meaning outside a viewset, so it
    is ``None`` here. ``kwargs`` is a fresh dict per check, so a permission
    that writes into it cannot carry a value into the next one
    (``test_a_permission_writing_view_kwargs_does_not_reach_the_next_check``).
    """

    def __init__(self, *, request: Request, kwargs: Mapping[str, Any]) -> None:
        self.request: Request = request
        self.action: str | None = None
        self.kwargs: dict[str, Any] = dict(kwargs)


def _wrap_request(http_request: HttpRequest, *, user: Any, auth: Any) -> Request:
    """Wrap an ``HttpRequest`` as a DRF ``Request`` with the supplied auth state.

    ``Request(http_request)`` is the canonical DRF upgrade path; ``.user`` and
    ``.auth`` are both set explicitly so MCP-supplied auth state flows through
    without DRF re-running its own ``authenticators`` chain. Setting only
    ``user`` leaves ``auth`` unresolved, and the first read of it runs that
    chain and resets ``user`` to ``AnonymousUser``.

    ``auth`` is the token backend's opaque payload, which is what a DRF
    permission expects to find there — an ``AccessToken`` row for
    django-oauth-toolkit, a claims dict for a JWT backend. ``None`` for a
    backend that publishes none, which is a value rather than a trigger.
    """
    # Constructed via ``Any`` and cast back to keep the static type.
    raw: Any = Request(http_request)
    drf_request: Request = cast(Request, raw)
    drf_request.user = user
    drf_request.auth = auth
    return drf_request


__all__ = ["DRFPermissionAdapter"]
