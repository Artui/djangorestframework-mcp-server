from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)


def _request_with_auth(header_value: str) -> HttpRequest:
    req = HttpRequest()
    req.META["HTTP_AUTHORIZATION"] = header_value
    return req


def test_no_authorization_header_returns_none() -> None:
    assert DjangoOAuthToolkitBackend().authenticate(HttpRequest()) is None


def test_non_bearer_header_returns_none() -> None:
    assert DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Basic abc")) is None


def test_empty_bearer_token_returns_none() -> None:
    assert DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer ")) is None


class _FakeToken:
    def __init__(
        self, *, scope: str, user: object, valid: bool, resource: str | None = None
    ) -> None:
        self.scope = scope
        self.user = user
        self._valid = valid
        self.resource = resource

    def is_valid(self) -> bool:
        return self._valid


def _patch_loader(monkeypatch, behaviour: Any) -> None:
    from oauth2_provider import oauth2_validators

    def factory(self):  # noqa: ARG001 - signature must match validator
        return None

    monkeypatch.setattr(
        oauth2_validators.OAuth2Validator,
        "_load_access_token",
        lambda self, token: behaviour(token),
        raising=False,
    )


def test_invalid_token_returns_none(monkeypatch) -> None:
    _patch_loader(monkeypatch, lambda _t: None)
    out = DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer x"))
    assert out is None


def test_loader_exception_returns_none(monkeypatch) -> None:
    def raise_exc(_t: str) -> None:
        raise RuntimeError("boom")

    _patch_loader(monkeypatch, raise_exc)
    out = DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer x"))
    assert out is None


def test_invalid_token_object_returns_none(monkeypatch) -> None:
    _patch_loader(
        monkeypatch,
        lambda _t: _FakeToken(scope="a b", user="u", valid=False),
    )
    out = DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer x"))
    assert out is None


def test_valid_token_returns_token_info_with_scopes(monkeypatch) -> None:
    _patch_loader(
        monkeypatch,
        lambda _t: _FakeToken(
            scope="read write", user="alice", valid=True, resource="https://x/mcp"
        ),
    )
    out = DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer x"))
    assert out is not None
    assert out.user == "alice"
    assert out.scopes == ("read", "write")
    assert out.audience == "https://x/mcp"


def test_valid_token_with_no_scope(monkeypatch) -> None:
    _patch_loader(monkeypatch, lambda _t: _FakeToken(scope="", user="alice", valid=True))
    out = DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer x"))
    assert out is not None
    assert out.scopes == ()


def test_audience_mismatch_rejects_token(monkeypatch, settings) -> None:
    """RFC 8707: a token bound to a different resource is rejected."""
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": "https://example.com/mcp/"}
    _patch_loader(
        monkeypatch,
        lambda _t: _FakeToken(
            scope="read", user="alice", valid=True, resource="https://other.example/mcp/"
        ),
    )
    out = DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer x"))
    assert out is None


def test_audience_match_accepts_token(monkeypatch, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": "https://example.com/mcp/"}
    _patch_loader(
        monkeypatch,
        lambda _t: _FakeToken(
            scope="read", user="alice", valid=True, resource="https://example.com/mcp/"
        ),
    )
    out = DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer x"))
    assert out is not None
    assert out.audience == "https://example.com/mcp/"


def test_audience_unset_skips_enforcement(monkeypatch, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    _patch_loader(
        monkeypatch,
        lambda _t: _FakeToken(scope="read", user="alice", valid=True, resource=None),
    )
    out = DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer x"))
    assert out is not None


def test_audience_set_but_token_missing_resource_rejected(monkeypatch, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": "https://example.com/mcp/"}
    _patch_loader(
        monkeypatch,
        lambda _t: _FakeToken(scope="read", user="alice", valid=True, resource=None),
    )
    out = DjangoOAuthToolkitBackend().authenticate(_request_with_auth("Bearer x"))
    assert out is None


def test_protected_resource_metadata_uses_settings(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "SERVER_INFO": {
            "resource": "https://x/mcp",
            "authorization_servers": ["https://x/auth"],
            "scopes_supported": ["read"],
            "documentation": "https://x/docs",
        }
    }
    md = DjangoOAuthToolkitBackend().protected_resource_metadata()
    assert md.resource == "https://x/mcp"
    assert md.authorization_servers == ["https://x/auth"]
    assert md.scopes_supported == ["read"]
    assert md.resource_documentation == "https://x/docs"


def test_protected_resource_metadata_prefers_resource_url(settings) -> None:
    """``RESOURCE_URL`` overrides the legacy ``SERVER_INFO["resource"]`` field.

    Single source of truth for the canonical URL so audience enforcement and
    PRM can never drift apart.
    """
    settings.REST_FRAMEWORK_MCP = {
        "RESOURCE_URL": "https://canonical.example/mcp/",
        "SERVER_INFO": {"resource": "https://stale.example/mcp/"},
    }
    md = DjangoOAuthToolkitBackend().protected_resource_metadata()
    assert md.resource == "https://canonical.example/mcp/"


def test_protected_resource_metadata_omits_documentation_when_unset(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"SERVER_INFO": {"resource": "https://x/mcp"}}
    md = DjangoOAuthToolkitBackend().protected_resource_metadata()
    assert md.resource_documentation is None
    # ``to_dict()`` drops the key entirely so the wire shape is clean.
    assert "resource_documentation" not in md.to_dict()


def test_protected_resource_metadata_emits_documentation_when_set(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "SERVER_INFO": {"resource": "https://x/mcp", "documentation": "https://x/docs"},
    }
    md = DjangoOAuthToolkitBackend().protected_resource_metadata()
    assert md.to_dict()["resource_documentation"] == "https://x/docs"


def test_protected_resource_metadata_required_keys_present(settings) -> None:
    """RFC 9728 — the four keys clients rely on are always emitted."""
    settings.REST_FRAMEWORK_MCP = {
        "RESOURCE_URL": "https://x/mcp/",
        "SERVER_INFO": {
            "authorization_servers": ["https://as.example/"],
            "scopes_supported": ["a"],
        },
    }
    md = DjangoOAuthToolkitBackend().protected_resource_metadata()
    payload = md.to_dict()
    for key in (
        "resource",
        "authorization_servers",
        "bearer_methods_supported",
        "scopes_supported",
    ):
        assert key in payload, f"PRM missing required key {key!r}"
    assert md.bearer_methods_supported == ["header"]


def test_www_authenticate_challenge_includes_metadata(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "SERVER_INFO": {"resource_metadata_url": "https://x/.well-known/oauth-protected-resource"},
    }
    challenge = DjangoOAuthToolkitBackend().www_authenticate_challenge(
        scopes=["a"], error="invalid_token"
    )
    assert "resource_metadata=" in challenge
    assert 'error="invalid_token"' in challenge
    assert 'scope="a"' in challenge


def test_www_authenticate_challenge_minimal(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"SERVER_INFO": {}}
    assert DjangoOAuthToolkitBackend().www_authenticate_challenge() == 'Bearer realm="mcp"'


# ---------- authorization_server_metadata (Phase 10d) ----------


def test_authorization_server_metadata_shape(settings) -> None:
    """RFC 8414 payload derived from ``SERVER_INFO``."""
    settings.REST_FRAMEWORK_MCP = {
        "SERVER_INFO": {
            "authorization_servers": ["https://issuer.example/oauth"],
            "scopes_supported": ["mcp:read", "mcp:write"],
        },
    }
    md = DjangoOAuthToolkitBackend().authorization_server_metadata()
    assert md.issuer == "https://issuer.example/oauth"
    assert md.authorization_endpoint == "https://issuer.example/oauth/oauth/authorize/"
    assert md.token_endpoint == "https://issuer.example/oauth/oauth/token/"
    assert md.registration_endpoint == "https://issuer.example/oauth/oauth/register/"
    assert "authorization_code" in md.grant_types_supported
    assert md.response_types_supported == ["code"]
    assert md.code_challenge_methods_supported == ["S256"]
    assert md.scopes_supported == ["mcp:read", "mcp:write"]


def test_authorization_server_metadata_missing_issuer_returns_empty_endpoints(settings) -> None:
    """No ``authorization_servers`` → empty issuer + endpoints, but valid JSON shape."""
    settings.REST_FRAMEWORK_MCP = {"SERVER_INFO": {}}
    md = DjangoOAuthToolkitBackend().authorization_server_metadata()
    assert md.issuer == ""
    assert md.authorization_endpoint == ""
    assert md.token_endpoint == ""


def test_authorization_server_metadata_strips_trailing_slash_in_issuer(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "SERVER_INFO": {"authorization_servers": ["https://issuer.example/oauth/"]},
    }
    md = DjangoOAuthToolkitBackend().authorization_server_metadata()
    # Trailing slash is normalised so endpoints don't double-slash.
    assert md.token_endpoint == "https://issuer.example/oauth/oauth/token/"
