from __future__ import annotations

from typing import Any

from django.urls import Resolver404, resolve

from rest_framework_mcp.observability import get_logger

logger = get_logger(__name__)

# The paths this package and ``oauth2_provider`` both serve as of DOT 3.4.0.
# Django resolves first-match, so whichever ``urlpatterns`` entry comes first
# silently wins and the other is dead.
_CONTESTED_PATHS: tuple[str, ...] = (
    "/oauth/register/",
    "/.well-known/oauth-authorization-server",
    "/oauth/.well-known/oauth-authorization-server",
)


def check_oauth_url_shadowing(*, warn: bool = True) -> list[str]:
    """Report contested OAuth paths that resolve to something other than ours.

    **Why this is a function you call rather than a Django system check:**
    ``rest_framework_mcp`` is a library, not an installed app — it has no
    ``AppConfig``, so there is nowhere to register one. Call it from your own
    check, a startup hook, or a test::

        def test_our_oauth_routes_are_not_shadowed():
            assert check_oauth_url_shadowing() == []

    **The trap.** django-oauth-toolkit 3.4.0 grew its own ``register/``
    (RFC 7591) and ``.well-known/oauth-authorization-server`` (RFC 8414). If
    ``include("oauth2_provider.urls")`` is mounted *before*
    :func:`build_oauth_urlpatterns`'s output, DOT answers those paths instead —
    with an issuer of ``<host>/oauth``, which is also the value that breaks
    :meth:`DjangoOAuthToolkitBackend.authorization_server_metadata`. Nothing
    errors; clients just get the wrong document.

    Returns the contested paths that resolve elsewhere, empty when all is well.
    A path that resolves nowhere is **not** reported: not mounting the OAuth
    surface at all is a legitimate configuration.
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
