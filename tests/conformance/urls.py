"""URL conf for the conformance suite — the full surface, in one place.

Mounted via ``@pytest.mark.urls("tests.conformance.urls")`` on
conformance tests so each test's Django client can drive the actual
URL resolution rather than calling views directly. The DOT urls and
the contrib OAuth mount land at the root so the contrib alias matrix
resolves at the canonical well-known paths.
"""

from __future__ import annotations

from django.urls import include, path

from rest_framework_mcp.contrib.oauth import build_oauth_urlpatterns
from tests.conformance.mcp import build_conformance_server

server = build_conformance_server()

urlpatterns: list = [
    path("mcp/", server.urls),
    *build_oauth_urlpatterns(
        server=server,
        include_dcr=True,
        include_aliases=True,
        include_openid_discovery=True,
        include_authorize=True,
    ),
    # DOT's own URLs are required for ``/oauth/token/`` and the model URLs
    # the DCR-created ``Application`` needs to function. Mounted under the
    # root since the contrib mount also lives there.
    path("o/", include("oauth2_provider.urls", namespace="oauth2_provider")),
]
