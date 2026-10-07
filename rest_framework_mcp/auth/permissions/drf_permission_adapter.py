from __future__ import annotations

import copy
from typing import Any

from django.http import HttpRequest
from rest_framework.permissions import BasePermission
from rest_framework_services.types.offline_context import OfflineContext

from rest_framework_mcp.auth.permissions.utils import DispatchShape
from rest_framework_mcp.auth.types.token_info import TokenInfo


class DRFPermissionAdapter:
    """Bridge a DRF ``BasePermission`` class into the
    [`MCPPermission`][rest_framework_mcp.auth.permissions.types.mcp_permission.MCPPermission]
    Protocol.

    ``ServiceSpec`` / ``SelectorSpec`` carry ``permission_classes`` as DRF
    ``BasePermission`` *classes*, and the MCP transport doesn't go through DRF
    views, so each class is wrapped here at registration time and instantiated
    once — mirroring what a DRF view's ``get_permissions`` does.

    The DRF instance is judged against a request and view built the way the
    spec's dispatch builds its own: from an internal ``DispatchShape``, through
    the same ``DispatchShape.build``, with ``user`` set to
    ``token.user`` and ``auth`` to the backend's opaque payload. So on
    ``tools/call`` the stand-in's ``request.data`` is the call's arguments less
    its route and query values, ``request.query_params`` the routed
    ``QueryParam`` values, ``view.kwargs`` the route and ``view.action`` the
    tool's name, as on the dispatch view, and its method is ``POST``, as there
    (``test_the_stand_in_sees_what_the_dispatch_view_sees``). It parsed the
    JSON-RPC body with no parsers once, so a permission reading
    ``request.data`` raised ``UnsupportedMediaType`` and every wire call it
    judged was a 500.

    **``auth`` is set alongside ``user``, and has to be.** DRF resolves
    ``request.auth`` lazily: reading it on a request that has never
    authenticated runs the (here empty) authenticator chain, which ends in
    ``_not_authenticated()`` and *overwrites* ``request.user`` with
    ``UNAUTHENTICATED_USER``. A permission class as ordinary as
    ``TokenHasScope`` reads ``request.auth`` first and every ``request.user``
    read after it would see ``AnonymousUser``, denying a properly scoped caller
    with nothing in the response explaining why. ``DispatchShape.build`` sets
    it, for the dispatch view as much as for this stand-in.

    **What a check is judged against is bound per call.** The adapter is built
    once at registration, where no call has named anything yet, so its own
    shape is empty: ``{}`` for ``request.data`` and ``view.kwargs``, the
    endpoint's own query string, and ``None`` for ``view.action``. That is what
    a check naming no call judges, a ``tools/list`` filtered by permission
    among them. Every check that names one, a ``tools/call``'s arguments or the
    variables of the URI a ``resources/read`` names, is made against a copy
    carrying it; the transport makes those copies itself, wherever it judges a
    binding's permissions.
    """

    def __init__(self, permission_class: type[BasePermission]) -> None:
        self._permission_class: type[BasePermission] = permission_class
        self._instance: BasePermission = permission_class()
        # Empty until ``_bound_to`` hands a copy a call's shape; a check made
        # with the registered adapter itself judges an empty route and no data
        # (``test_an_unbound_adapter_judges_an_empty_route``).
        self._shape: DispatchShape = DispatchShape()

    @property
    def permission_class(self) -> type[BasePermission]:
        return self._permission_class

    def _bound_to(self, shape: DispatchShape) -> DRFPermissionAdapter:
        # This adapter as one check judges it, against the call the request
        # names. Private on purpose, with one caller: ``check_permissions`` in
        # ``handlers/utils.py`` calls it for every adapter when it is handed
        # a shape, so a call is bound where a check is made rather than by
        # every path that makes one.
        #
        # A copy rather than this adapter, which every concurrent call to the
        # binding shares: a route written onto it would be judged on another
        # caller's call (``test_the_registered_adapter_is_left_unbound``).
        # ``copy.copy`` rather than the constructor, so a subclass keeps what
        # its own ``__init__`` set and the permission is not instantiated again
        # (``test_the_permission_is_not_instantiated_again_nor_a_subclass_state_dropped``);
        # the copies share the wrapped DRF instance, as every call already does.
        bound: DRFPermissionAdapter = copy.copy(self)
        # Held as given: ``DispatchShape.build`` copies its ``kwargs`` into a
        # fresh view for every check, which is the one copy that keeps a
        # permission's writes off the caller's mapping and out of the next
        # check.
        bound._shape = shape
        return bound

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        context: OfflineContext = self._shape.build(
            user=token.user, auth=token.raw, http_request=request
        )
        # The DRF stub types the second argument as ``APIView``; the stand-in is
        # structural, so it is typed ``Any`` at this one boundary to keep the
        # rest of the package statically typed.
        view: Any = context.view
        return bool(self._instance.has_permission(context.request, view))

    def required_scopes(self) -> list[str]:
        # DRF permissions carry no OAuth-scope semantics; a subclass or a
        # sibling ``MCPPermission`` is where scope requirements surface.
        return []


__all__ = ["DRFPermissionAdapter"]
