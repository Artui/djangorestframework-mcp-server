"""Coverage: the full opt-in URL matrix assembled by ``build_oauth_urlpatterns``.

Verifies that every endpoint we advertise actually resolves and that the
flags (``include_aliases`` / ``include_openid_discovery`` / ``include_dcr``)
toggle the right subset.
"""

from __future__ import annotations

from typing import Any

from django.urls import URLPattern

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)
from rest_framework_mcp.contrib.oauth.build_oauth_urlpatterns import build_oauth_urlpatterns
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server(*, backend: Any = None) -> MCPServer:
    return MCPServer(
        name="t",
        auth_backend=backend or AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )


def _pattern_strings(patterns: list[URLPattern]) -> set[str]:
    """Return the string form of each pattern's ``pattern`` attribute."""
    return {str(p.pattern) for p in patterns}


def test_default_emits_canonical_endpoints_only() -> None:
    """Without flag overrides, the default mount carries: PRM, AS metadata,
    OIDC discovery, plus their aliases — no DCR."""
    patterns = build_oauth_urlpatterns(server=_server())
    strs = _pattern_strings(patterns)
    # Canonical endpoints.
    assert ".well-known/oauth-protected-resource" in strs
    assert ".well-known/oauth-authorization-server" in strs
    assert ".well-known/openid-configuration" in strs
    # Aliases (default include_aliases=True).
    assert ".well-known/oauth-protected-resource/mcp" in strs
    assert "mcp/.well-known/oauth-protected-resource" in strs
    assert ".well-known/oauth-authorization-server/oauth" in strs
    assert "oauth/.well-known/oauth-authorization-server" in strs
    assert ".well-known/openid-configuration/oauth" in strs
    # DCR is opt-in.
    assert "oauth/register/" not in strs


def test_include_aliases_false_drops_alias_endpoints() -> None:
    patterns = build_oauth_urlpatterns(server=_server(), include_aliases=False)
    strs = _pattern_strings(patterns)
    # Canonicals still present.
    assert ".well-known/oauth-protected-resource" in strs
    assert ".well-known/oauth-authorization-server" in strs
    assert ".well-known/openid-configuration" in strs
    # Aliases removed.
    assert ".well-known/oauth-protected-resource/mcp" not in strs
    assert "mcp/.well-known/oauth-protected-resource" not in strs
    assert ".well-known/oauth-authorization-server/oauth" not in strs
    assert "oauth/.well-known/oauth-authorization-server" not in strs
    assert ".well-known/openid-configuration/oauth" not in strs


def test_include_openid_discovery_false_drops_oidc() -> None:
    patterns = build_oauth_urlpatterns(server=_server(), include_openid_discovery=False)
    strs = _pattern_strings(patterns)
    assert ".well-known/openid-configuration" not in strs
    assert ".well-known/openid-configuration/oauth" not in strs


def test_include_dcr_true_mounts_register_endpoint() -> None:
    patterns = build_oauth_urlpatterns(server=_server(), include_dcr=True)
    strs = _pattern_strings(patterns)
    assert "oauth/register/" in strs


def test_include_authorize_true_mounts_authorize_endpoint() -> None:
    patterns = build_oauth_urlpatterns(server=_server(), include_authorize=True)
    strs = _pattern_strings(patterns)
    assert "oauth/authorize/" in strs


def test_include_authorize_false_does_not_mount_authorize_endpoint() -> None:
    patterns = build_oauth_urlpatterns(server=_server())
    strs = _pattern_strings(patterns)
    assert "oauth/authorize/" not in strs


def test_pattern_names_are_namespaced() -> None:
    """Every URL pattern carries a ``name`` so reverse() works."""
    patterns = build_oauth_urlpatterns(server=_server(), include_dcr=True, include_authorize=True)
    names = {p.name for p in patterns}
    assert "mcp-oauth-prm" in names
    assert "mcp-oauth-as-metadata" in names
    assert "mcp-oauth-oidc-discovery" in names
    assert "mcp-oauth-dcr" in names
    assert "mcp-oauth-authorize" in names
    assert "mcp-oauth-prm-mcp-alias" in names


def test_backend_threads_through_to_metadata_views(settings) -> None:
    """The PRM / AS / OIDC views receive the same backend instance."""
    settings.REST_FRAMEWORK_MCP = {
        "SERVER_INFO": {"authorization_servers": ["https://issuer.example/oauth"]},
    }
    backend = DjangoOAuthToolkitBackend()
    server = _server(backend=backend)
    patterns = build_oauth_urlpatterns(server=server)
    # Inspect the view-class instance hung on the resolved view callable.
    by_name = {p.name: p for p in patterns}
    as_metadata = by_name["mcp-oauth-as-metadata"]
    # DRF ViewSet's ``as_view`` stashes the per-mount kwargs on the
    # callable as ``initkwargs`` (without the ``view_`` prefix Django's
    # plain ``View.as_view`` uses).
    assert as_metadata.callback.initkwargs["auth_backend"] is backend


def test_alias_renders_not_redirects(settings) -> None:
    """Aliases serve the canonical payload byte-for-byte rather than HTTP-redirecting."""
    from django.test import RequestFactory

    settings.REST_FRAMEWORK_MCP = {
        "SERVER_INFO": {"authorization_servers": ["https://issuer.example/oauth"]},
    }
    server = _server(backend=DjangoOAuthToolkitBackend())
    patterns = build_oauth_urlpatterns(server=server)
    by_name = {p.name: p for p in patterns}
    canonical = by_name["mcp-oauth-as-metadata"].callback(
        RequestFactory().get("/.well-known/oauth-authorization-server")
    )
    alias = by_name["mcp-oauth-as-metadata-oauth-alias"].callback(
        RequestFactory().get("/.well-known/oauth-authorization-server/oauth")
    )
    assert canonical.status_code == 200
    assert alias.status_code == 200
    # DRF ``Response`` is lazy — ``.data`` is the pre-render dict; we
    # compare that rather than ``.content`` to avoid the
    # ``ContentNotRenderedError`` you get from invoking the view via
    # ``RequestFactory`` without going through Django's middleware.
    assert canonical.data == alias.data
