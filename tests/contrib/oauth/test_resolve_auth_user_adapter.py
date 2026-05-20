from __future__ import annotations

from rest_framework_mcp.contrib.oauth.adapters.simplejwt_cookie import SimpleJWTCookieAdapter
from rest_framework_mcp.contrib.oauth.resolve_auth_user_adapter import resolve_auth_user_adapter


def test_returns_none_when_unset(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    assert resolve_auth_user_adapter() is None


def test_resolves_dotted_path(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "AUTH_USER_ADAPTER": (
            "rest_framework_mcp.contrib.oauth.adapters.simplejwt_cookie.SimpleJWTCookieAdapter"
        ),
    }
    adapter = resolve_auth_user_adapter()
    assert isinstance(adapter, SimpleJWTCookieAdapter)
