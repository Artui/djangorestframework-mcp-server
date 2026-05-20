"""Factory for the DOT ``AuthorizationView`` subclass with an adapter hook.

The view itself can't be defined at module load — DOT (``oauth2_provider``)
is an optional extra. The factory lazy-imports DOT and returns a freshly-
built subclass parameterised by the supplied adapter.

This is the one documented exception to the "always ViewSet, never View"
rule in `CLAUDE.md` §13: ``AuthorizationView`` lives in DOT, which we
don't own, and converting it to a ViewSet would mean reimplementing the
entire OAuth authorization flow. The subclass is wafer-thin — just a
``dispatch`` override that calls the adapter — so the cost of staying on
the View base class is minimal.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse

from rest_framework_mcp.contrib.oauth.adapters.types.auth_user_adapter import AuthUserAdapter


def build_authorize_passthrough_view(adapter: AuthUserAdapter | None) -> Any:
    """Return a ``View`` callable suitable for ``urlpatterns``.

    Concretely: instantiates DOT's :class:`AuthorizationView` subclass
    with the adapter baked in and calls ``.as_view()``. The resulting
    callable matches the shape that
    :func:`django.urls.path` / :func:`django.urls.re_path` expect.

    When ``adapter`` is ``None`` the passthrough is functionally
    identical to DOT's ``AuthorizationView`` — safe to mount in every
    deployment regardless of whether the consumer plans to enable
    hydration later.
    """
    try:
        from oauth2_provider.views import AuthorizationView  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised by smoke job w/o DOT
        raise ImportError(
            "build_authorize_passthrough_view requires `django-oauth-toolkit`. "
            'Install it via `pip install "djangorestframework-mcp-server[oauth]"`.'
        ) from exc

    class _AuthorizePassthroughView(AuthorizationView):  # type: ignore[misc, valid-type]
        """DOT ``AuthorizationView`` with a pre-dispatch user-hydration hook.

        ``dispatch`` is the right injection point because it runs before
        DOT's permission check / form rendering. Setting
        ``request.user`` to the adapter-resolved user lets DOT treat the
        request as authenticated without a session-based redirect.
        """

        # Bind the adapter into the class body so the closure semantics
        # match DOT's expectation that views are stateless.
        _auth_user_adapter: AuthUserAdapter | None = adapter

        def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if self._auth_user_adapter is not None:
                user = self._auth_user_adapter.hydrate(request)
                if user is not None:
                    request.user = user
            return super().dispatch(request, *args, **kwargs)

    return _AuthorizePassthroughView.as_view()


__all__ = ["build_authorize_passthrough_view"]
