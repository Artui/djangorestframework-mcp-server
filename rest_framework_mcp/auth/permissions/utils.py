from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.http import HttpRequest
from rest_framework_services import build_offline_context
from rest_framework_services.types.offline_context import OfflineContext


@dataclass(frozen=True)
class DispatchShape:
    """The values a call's dispatch request and view are built from.

    A spec's ``permission_classes`` are judged twice on ``tools/call``: by the
    binding's wrapped
    [`DRFPermissionAdapter`][rest_framework_mcp.auth.permissions.drf_permission_adapter.DRFPermissionAdapter],
    against a stand-in, and by ``enforce_permissions``, against the request and
    view the spec is dispatched with. Both are built by ``build``, from one of
    these, so the stand-in cannot see another request than the dispatch view
    does. It once did: its ``request.data`` parsed the JSON-RPC body with no
    parsers, so a permission reading it raised ``UnsupportedMediaType`` and
    every wire call was a 500; its ``query_params`` were the endpoint's query
    string; and its ``view.action`` was ``None``.

    The layout is DRF's, on every route: a route capture in ``kwargs``, a
    ``QueryParam``'s value in ``query_params``, and the rest of the arguments in
    ``data``. ``handlers.utils.dispatch_shape`` is what splits a call's
    arguments into it.

    Internal infrastructure rather than API: the transport builds these, and a
    consumer never names one.
    """

    # ``Any`` because ``request.data`` is whatever the call carries: a mapping
    # of the arguments, or ``{}`` for a check that names no call.
    data: Any = field(default_factory=dict)
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    # ``None`` keeps the wrapped request's own query string, for a check that
    # names no call; a mapping replaces it, empty or not.
    query_params: Mapping[str, Any] | None = None
    action: str | None = None

    def build(self, *, user: Any, auth: Any, http_request: HttpRequest | None) -> OfflineContext:
        """The request and view a check or a dispatch is made against.

        Through drf-services' ``build_offline_context``, so the request wraps a
        copy of ``http_request`` (never written to), its method is ``POST``,
        ``data`` is seeded rather than parsed, and ``view.kwargs`` is a fresh
        ``dict`` per build, which a permission writing into it cannot carry
        into the next check.

        **``auth`` is set beside ``user``, and has to be.** DRF resolves
        ``request.auth`` lazily: reading it on a request that has never
        authenticated runs the (here empty) authenticator chain, which ends in
        ``_not_authenticated()`` and overwrites ``request.user`` with
        ``AnonymousUser``. A permission as ordinary as ``TokenHasScope`` reads
        ``request.auth`` first, so every ``request.user`` read after it saw an
        anonymous caller. ``auth`` is the token backend's opaque payload, DRF's
        convention for what it holds, and ``None`` for a backend that publishes
        none, which is a value rather than a trigger
        (``test_a_dispatch_view_reading_auth_keeps_the_caller``).
        """
        context: OfflineContext = build_offline_context(
            user,
            self.data,
            http_request=http_request,
            action=self.action,
            kwargs=self.kwargs,
            query_params=self.query_params,
        )
        context.request.auth = auth
        return context


__all__ = ["DispatchShape"]
