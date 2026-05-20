from __future__ import annotations

from django.test import RequestFactory

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)
from rest_framework_mcp.contrib.oauth.openid_discovery_viewset import OpenIDDiscoveryViewSet


def test_get_returns_as_metadata_plus_oidc_defaults(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "SERVER_INFO": {"authorization_servers": ["https://issuer.example/oauth"]},
    }
    view = OpenIDDiscoveryViewSet.as_view({"get": "list"}, auth_backend=DjangoOAuthToolkitBackend())
    response = view(RequestFactory().get("/.well-known/openid-configuration"))
    assert response.status_code == 200
    body = response.data
    # AS metadata fields survive…
    assert body["issuer"] == "https://issuer.example/oauth"
    # …plus OIDC additions.
    assert body["subject_types_supported"] == ["public"]
    assert "id_token_signing_alg_values_supported" in body
    assert body["response_modes_supported"] == ["query"]


def test_get_returns_501_when_backend_has_no_authorization_server() -> None:
    view = OpenIDDiscoveryViewSet.as_view({"get": "list"}, auth_backend=AllowAnyBackend())
    response = view(RequestFactory().get("/.well-known/openid-configuration"))
    assert response.status_code == 501
