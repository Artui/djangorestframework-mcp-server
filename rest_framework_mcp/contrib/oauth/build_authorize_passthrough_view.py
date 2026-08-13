"""Factory for the DOT ``AuthorizationView`` subclass with an adapter hook.

The view cannot be defined at module load, because DOT (``oauth2_provider``)
is an optional extra; the factory lazy-imports it and returns a subclass
parameterised by the supplied adapter.

The one documented exception to the "always ViewSet, never View" rule in
``CLAUDE.md`` §13: ``AuthorizationView`` belongs to DOT, and converting it
would mean reimplementing the whole OAuth authorization flow. The subclass is
one ``dispatch`` override.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse

from rest_framework_mcp.contrib.oauth.adapters.types.auth_user_adapter import AuthUserAdapter


def build_authorize_passthrough_view(adapter: AuthUserAdapter | None) -> Any:
    """Return a ``View`` callable suitable for ``urlpatterns``.

    Builds DOT's ``AuthorizationView`` subclass with the adapter baked in
    and calls ``.as_view()``. A ``None`` adapter makes the passthrough
    functionally identical to DOT's own view, so it is safe to mount whether or
    not hydration is wanted later.
    """
    try:
        from oauth2_provider.views import AuthorizationView
    except ImportError as exc:  # pragma: no cover - exercised by smoke job w/o DOT
        raise ImportError(
            "build_authorize_passthrough_view requires `django-oauth-toolkit`. "
            'Install it via `pip install "djangorestframework-mcp-server[oauth]"`.'
        ) from exc

    class _AuthorizePassthroughView(AuthorizationView):
        """DOT ``AuthorizationView`` with a pre-dispatch user-hydration hook.

        ``dispatch`` is the injection point because it runs before DOT's
        permission check and form rendering, so setting ``request.user`` there
        lets DOT treat the request as authenticated without a session-based
        redirect.
        """

        # Bound into the class body rather than closed over, matching DOT's
        # expectation that views are stateless.
        _auth_user_adapter: AuthUserAdapter | None = adapter

        def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if self._auth_user_adapter is not None:
                user = self._auth_user_adapter.hydrate(request)
                if user is not None:
                    request.user = user
            return super().dispatch(request, *args, **kwargs)

    return _AuthorizePassthroughView.as_view()


__all__ = ["build_authorize_passthrough_view"]
