"""End-to-end coverage of every URL in the contrib OAuth endpoint matrix.

Drives Django's URL resolver against the conformance URL conf and
verifies each canonical path + alias serves the expected JSON shape.
The conformance server uses :class:`AllowAnyBackend`, which raises
``NotImplementedError`` for AS metadata — the AS / OIDC endpoints are
asserted to return ``501`` (the documented "no AS configured" signal).
The PRM endpoints serve a payload regardless.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.urls("tests.conformance.urls")


# ---------- PRM (the only AS-agnostic endpoints) ----------


def test_prm_canonical_path_responds_200(client) -> None:
    response = client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert "bearer_methods_supported" in body


def test_prm_alias_mcp_responds_200(client) -> None:
    response = client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200


def test_prm_alias_local_responds_200(client) -> None:
    response = client.get("/mcp/.well-known/oauth-protected-resource")
    assert response.status_code == 200


def test_prm_aliases_render_canonical_payload(client) -> None:
    """Aliases serve the same bytes as the canonical — no redirect."""
    canonical = client.get("/.well-known/oauth-protected-resource").content
    alias_a = client.get("/.well-known/oauth-protected-resource/mcp").content
    alias_b = client.get("/mcp/.well-known/oauth-protected-resource").content
    assert canonical == alias_a == alias_b


# ---------- AS metadata (501 under AllowAnyBackend) ----------


def test_as_metadata_canonical_returns_501_for_allow_any_backend(client) -> None:
    response = client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 501
    body = json.loads(response.content)
    assert body["error"] == "authorization_server_unavailable"


def test_as_metadata_oauth_alias_returns_501(client) -> None:
    response = client.get("/.well-known/oauth-authorization-server/oauth")
    assert response.status_code == 501


def test_as_metadata_local_alias_returns_501(client) -> None:
    response = client.get("/oauth/.well-known/oauth-authorization-server")
    assert response.status_code == 501


# ---------- OIDC discovery (same backstop) ----------


def test_oidc_discovery_canonical_returns_501(client) -> None:
    response = client.get("/.well-known/openid-configuration")
    assert response.status_code == 501


def test_oidc_discovery_alias_returns_501(client) -> None:
    response = client.get("/.well-known/openid-configuration/oauth")
    assert response.status_code == 501
