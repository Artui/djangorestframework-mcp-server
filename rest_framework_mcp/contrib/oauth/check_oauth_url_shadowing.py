from __future__ import annotations

from typing import Any

from django.urls import Resolver404, resolve

from rest_framework_mcp.observability import get_logger

logger = get_logger(__name__)

# Paths this package and ``oauth2_provider`` both serve as of DOT 3.4.0, which
# the ``[oauth]`` extra now floors at -- so the collision is no longer something
# only an early adopter can hit.
#
# The four ``.well-known`` entries beyond the authorization-server document are
# the ones DOT's ``metadata_urlpatterns`` answer, and DOT's own documentation
# tells deployers to mount those **at the server root** so RFC 8414 and RFC 9728
# clients find them. That is precisely the arrangement that takes our protected
# resource metadata: DOT's ``<path:resource_path>`` / ``<path:issuer_path>``
# forms also swallow the alias paths, so listing the canonical URL alone would
# miss half of it. A client reading DOT's protected-resource document gets
# DOT's ``resource``, not this server's, and every audience-bound token it then
# asks for is minted for the wrong thing.
#
# ``/oauth/authorize/`` is deliberately **not** here even though both packages
# serve it. ``build_oauth_urlpatterns(include_authorize=False)`` is the default
# and the documented arrangement is for the consumer's own URLconf to own that
# path via ``include('oauth2_provider.urls')`` -- so DOT answering it is usually
# correct, and reporting it would fire on a configuration that is working as
# designed. ``/oauth/register/`` has the same shape (``include_dcr`` also
# defaults off) and is kept for the deployment that does mount DCR; that is a
# judgement about which way the ambiguity is worth resolving, not an oversight.
_CONTESTED_PATHS: tuple[str, ...] = (
    "/oauth/register/",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-authorization-server/oauth",
    "/oauth/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/mcp/.well-known/oauth-protected-resource",
    "/.well-known/openid-configuration",
    "/.well-known/openid-configuration/oauth",
)


def check_oauth_url_shadowing(*, warn: bool = True) -> list[str]:
    """Report contested OAuth paths that resolve to something other than ours.

    django-oauth-toolkit 3.4.0 serves its own ``register/`` (RFC 7591),
    ``.well-known/oauth-authorization-server`` (RFC 8414),
    ``.well-known/oauth-protected-resource`` (RFC 9728) and
    ``.well-known/openid-configuration``, plus the path-component forms of the
    two metadata documents. Django resolves first-match, so mounting
    ``include("oauth2_provider.urls")`` *before*
    [`build_oauth_urlpatterns`][rest_framework_mcp.contrib.oauth.build_oauth_urlpatterns.build_oauth_urlpatterns]'s
    output makes DOT answer those paths, with an issuer of ``<host>/oauth``. Nothing
    errors; clients just read the wrong document.

    A function rather than a Django system check because ``rest_framework_mcp``
    is a library with no ``AppConfig`` to register one on. Call it from your own
    check, a startup hook, or a test:

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
