"""URL conf for the conformance suite — the full surface, in one place.

Mounted via ``@pytest.mark.urls("tests.conformance.urls")`` on conformance tests
so each test's Django client can drive the actual URL resolution rather than
calling views directly. The DOT urls and the contrib OAuth mount land at the
root so the contrib alias matrix resolves at the canonical well-known paths.

The DCR gates and the authorize adapter resolve when the patterns are built, so
a test needing non-default ones calls :func:`conformance_urlconf` and mounts the
result — rather than mutating settings and reloading this module, which is what
the dotted-path indirection used to force.
"""

from __future__ import annotations

import types
from typing import Any

from django.urls import include, path

from rest_framework_mcp.contrib.oauth import build_oauth_urlpatterns
from rest_framework_mcp.contrib.oauth.adapters.types.auth_user_adapter import AuthUserAdapter
from tests.conformance.mcp import build_conformance_server


def build_conformance_urls(
    *,
    auth_user_adapter: AuthUserAdapter | None = None,
    dcr_enabled: bool | None = None,
    dcr_initial_access_token: str | None = None,
) -> list[Any]:
    server = build_conformance_server()
    return [
        path("mcp/", server.urls),
        *build_oauth_urlpatterns(
            server=server,
            include_dcr=True,
            include_aliases=True,
            include_openid_discovery=True,
            include_authorize=True,
            auth_user_adapter=auth_user_adapter,
            dcr_enabled=dcr_enabled,
            dcr_initial_access_token=dcr_initial_access_token,
        ),
        # DOT's own URLs are required for ``/oauth/token/`` and the model URLs
        # the DCR-created ``Application`` needs to function. Mounted under the
        # root since the contrib mount also lives there.
        path("o/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    ]


def conformance_urlconf(**kwargs: Any) -> types.ModuleType:
    """A throwaway conformance URL conf for ``override_settings(ROOT_URLCONF=...)``."""
    module = types.ModuleType("tests.conformance._dynamic_urls")
    module.urlpatterns = build_conformance_urls(**kwargs)  # type: ignore[attr-defined]
    return module


urlpatterns: list[Any] = build_conformance_urls()
