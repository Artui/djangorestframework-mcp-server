from __future__ import annotations

from django.test import RequestFactory

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)
from rest_framework_mcp.contrib.oauth.authorization_server_metadata_viewset import (
    AuthorizationServerMetadataViewSet,
)


def test_get_returns_backend_metadata(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "SERVER_INFO": {
            "authorization_servers": ["https://issuer.example/oauth"],
            "scopes_supported": ["mcp:read"],
        },
    }
    view = AuthorizationServerMetadataViewSet.as_view(
        {"get": "list"}, auth_backend=DjangoOAuthToolkitBackend()
    )
    response = view(RequestFactory().get("/.well-known/oauth-authorization-server"))
    assert response.status_code == 200
    body = response.data
    assert body["issuer"] == "https://issuer.example/oauth"
    assert body["registration_endpoint"].endswith("/oauth/register/")


def test_get_returns_501_when_backend_has_no_authorization_server() -> None:
    view = AuthorizationServerMetadataViewSet.as_view(
        {"get": "list"}, auth_backend=AllowAnyBackend()
    )
    response = view(RequestFactory().get("/.well-known/oauth-authorization-server"))
    assert response.status_code == 501
    body = response.data
    assert body["error"] == "authorization_server_unavailable"
