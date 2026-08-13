from __future__ import annotations

from typing import Any

from django.urls import Resolver404, resolve

from rest_framework_mcp.observability import get_logger

logger = get_logger(__name__)

# Paths this package and ``oauth2_provider`` both serve as of DOT 3.4.0.
_CONTESTED_PATHS: tuple[str, ...] = (
    "/oauth/register/",
    "/.well-known/oauth-authorization-server",
    "/oauth/.well-known/oauth-authorization-server",
)


def check_oauth_url_shadowing(*, warn: bool = True) -> list[str]:
    """Report contested OAuth paths that resolve to something other than ours.

    django-oauth-toolkit 3.4.0 serves its own ``register/`` (RFC 7591) and
    ``.well-known/oauth-authorization-server`` (RFC 8414). Django resolves first-match,
    so mounting ``include("oauth2_provider.urls")`` *before*
    [`build_oauth_urlpatterns`][rest_framework_mcp.contrib.oauth.build_oauth_urlpatterns.build_oauth_urlpatterns]'s
    output makes DOT answer those paths, with an issuer of ``<host>/oauth``. Nothing
    errors; clients just read the wrong document.

    A function rather than a Django system check because ``rest_framework_mcp``
    is a library with no ``AppConfig`` to register one on. Call it from your own
    check, a startup hook, or a test::

        def test_our_oauth_routes_are_not_shadowed():
            assert check_oauth_url_shadowing() == []

    Returns the contested paths that resolve elsewhere, and is empty when all
    is well. A path resolving nowhere is **not** reported — not mounting the
    OAuth surface at all is a legitimate configuration.
    """
    shadowed: list[str] = []
    for path in _CONTESTED_PATHS:
        try:
            match: Any = resolve(path)
        except Resolver404:
            continue
        module: str = getattr(match.func, "__module__", "") or ""
        if not module.startswith("rest_framework_mcp"):
            shadowed.append(path)
    if shadowed and warn:
        logger.warning(
            "OAuth discovery paths are shadowed by another app: %s. Mount "
            "build_oauth_urlpatterns(...) *before* include('oauth2_provider.urls') "
            "in urlpatterns — Django resolves first-match.",
            ", ".join(shadowed),
        )
    return shadowed


__all__ = ["check_oauth_url_shadowing"]
