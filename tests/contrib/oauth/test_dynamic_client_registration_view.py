"""OAuth DCR view behaviour: gating + happy path + errors."""

from __future__ import annotations

import json
import secrets

import pytest
from django.test import RequestFactory, override_settings

from rest_framework_mcp.contrib.oauth.dynamic_client_registration_viewset import (
    DynamicClientRegistrationViewSet,
)


def _post(
    body: bytes | str,
    *,
    auth_header: str | None = None,
    dcr_enabled: bool = True,
    initial_access_token: str | None = None,
) -> object:
    """Drive the DCR view with its gates set explicitly.

    ``build_oauth_urlpatterns`` resolves these from settings once and passes
    them via ``as_view``; a hand-wired view supplies its own.
    """
    factory = RequestFactory()
    request = factory.post(
        "/oauth/register/",
        data=body if isinstance(body, bytes) else body.encode("utf-8"),
        content_type="application/json",
    )
    if auth_header is not None:
        request.META["HTTP_AUTHORIZATION"] = auth_header
    view = DynamicClientRegistrationViewSet.as_view(
        {"post": "create"},
        dcr_enabled=dcr_enabled,
        initial_access_token=initial_access_token,
    )
    return view(request)


def test_disabled_by_default_returns_403() -> None:
    """The view's own default is off — a hand-wired view that forgets the gate
    refuses registrations rather than opening them."""
    response = _post('{"redirect_uris": ["https://x/cb"]}', dcr_enabled=False)
    assert response.status_code == 403
    assert response.data["error"] == "invalid_request"


def test_initial_access_token_required_when_set() -> None:
    response = _post('{"redirect_uris": ["https://x/cb"]}', initial_access_token="secret")
    assert response.status_code == 401
    body = response.data
    assert body["error"] == "invalid_token"


def test_initial_access_token_wrong_value_returns_401() -> None:
    response = _post(
        '{"redirect_uris": ["https://x/cb"]}',
        auth_header="Bearer wrong",
        initial_access_token="secret",
    )
    assert response.status_code == 401


def test_invalid_json_returns_400() -> None:
    response = _post("not-json")
    assert response.status_code == 400
    assert response.data["error"] == "invalid_request"


def test_invalid_schema_returns_400_with_detail() -> None:
    """Missing required ``redirect_uris`` → 400 with per-field detail."""
    response = _post("{}")
    assert response.status_code == 400
    body = response.data
    assert body["error"] == "invalid_client_metadata"
    assert "redirect_uris" in body["detail"]


@pytest.mark.django_db
def test_happy_path_creates_dot_application_and_returns_credentials() -> None:
    response = _post(
        json.dumps(
            {
                "redirect_uris": ["https://client.example/cb"],
                "client_name": "Test client",
            }
        )
    )
    assert response.status_code == 201
    body = response.data
    assert body["client_id"]
    assert body["client_secret"]
    assert body["client_name"] == "Test client"
    assert body["redirect_uris"] == ["https://client.example/cb"]
    # Defaults: confidential client + authorization_code grant.
    from oauth2_provider.models import Application

    assert body["client_type"] == Application.CLIENT_CONFIDENTIAL
    assert body["authorization_grant_type"] == Application.GRANT_AUTHORIZATION_CODE
    # The Application was actually persisted.
    assert Application.objects.filter(client_id=body["client_id"]).exists()


@pytest.mark.django_db
def test_happy_path_echoes_scope_when_provided() -> None:
    """``read`` / ``write`` are DOT's own default ``SCOPES``."""
    response = _post(
        json.dumps({"redirect_uris": ["https://client.example/cb"], "scope": "read write"})
    )
    assert response.status_code == 201
    body = response.data
    assert body["scope"] == "read write"


@pytest.mark.django_db
def test_scope_the_server_does_not_offer_is_rejected() -> None:
    """Registering an unavailable scope fails here rather than at authorize.

    DOT stores no per-application scope, so echoing one back unchecked would
    tell the client it registered something the authorization server will
    refuse a leg later, with nothing linking the two.
    """
    response = _post(
        json.dumps({"redirect_uris": ["https://client.example/cb"], "scope": "read mcp:admin"})
    )
    assert response.status_code == 400
    body = response.data
    assert body["error"] == "invalid_client_metadata"
    assert "mcp:admin" in body["detail"]["scope"][0]


@pytest.mark.django_db
def test_scope_check_follows_the_configured_scopes() -> None:
    """The check reads DOT's scopes backend, not a list of our own."""
    with override_settings(OAUTH2_PROVIDER={"SCOPES": {"mcp:read": "Read via MCP"}}):
        response = _post(
            json.dumps({"redirect_uris": ["https://client.example/cb"], "scope": "mcp:read"})
        )
        assert response.status_code == 201
        assert response.data["scope"] == "mcp:read"

        rejected = _post(
            json.dumps({"redirect_uris": ["https://client.example/cb"], "scope": "write"})
        )
        assert rejected.status_code == 400


@pytest.mark.django_db
def test_rejected_scope_leaves_no_application_row() -> None:
    from oauth2_provider.models import Application

    _post(json.dumps({"redirect_uris": ["https://client.example/cb"], "scope": "nope"}))
    assert not Application.objects.exists()


@pytest.mark.django_db
def test_happy_path_respects_explicit_client_type() -> None:
    from oauth2_provider.models import Application

    response = _post(
        json.dumps(
            {
                "redirect_uris": ["https://client.example/cb"],
                "client_type": Application.CLIENT_PUBLIC,
            }
        )
    )
    assert response.status_code == 201
    body = response.data
    assert body["client_type"] == Application.CLIENT_PUBLIC


@pytest.mark.django_db
def test_happy_path_with_matching_initial_access_token() -> None:
    """Token gating: present + correct → fall through to validation."""
    response = _post(
        json.dumps({"redirect_uris": ["https://client.example/cb"]}),
        auth_header="Bearer secret",
        initial_access_token="secret",
    )
    assert response.status_code == 201


@pytest.mark.django_db
def test_happy_path_without_initial_access_token() -> None:
    """``initial_access_token=None`` skips the token check entirely."""
    response = _post(json.dumps({"redirect_uris": ["https://client.example/cb"]}))
    assert response.status_code == 201


def test_invalid_client_type_choice_returns_400() -> None:
    response = _post(
        json.dumps({"redirect_uris": ["https://x/cb"], "client_type": "not-a-real-type"})
    )
    assert response.status_code == 400
    body = response.data
    assert "client_type" in body["detail"]


def test_the_initial_access_token_is_compared_in_constant_time(monkeypatch) -> None:
    """``!=`` returns at the first differing byte, and this is a bearer credential.

    An attacker probing the endpoint with candidate prefixes reads the
    response-time difference between a wrong first byte and N right ones;
    recovering the token turns a gated registration endpoint into an open one,
    which is the precondition for every other registration abuse. The comparison
    is pinned rather than timed because a timing assertion over a test runner is
    noise, and what is actually being fixed is which primitive is used.
    """
    seen: list[tuple[bytes, bytes]] = []
    real = secrets.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(
        "rest_framework_mcp.contrib.oauth.dynamic_client_registration_viewset.secrets"
        ".compare_digest",
        spy,
    )
    response = _post(
        '{"redirect_uris": ["https://x/cb"]}',
        auth_header="Bearer wrong",
        initial_access_token="secret",
    )
    assert response.status_code == 401
    assert seen == [(b"Bearer wrong", b"Bearer secret")]


def test_a_non_ascii_authorization_header_is_a_401_not_a_500() -> None:
    """``compare_digest`` raises ``TypeError`` on non-ASCII ``str``.

    The presented half is a caller-supplied header, so the comparison is done on
    bytes; otherwise the fix would trade a timing leak for a crash oracle.
    """
    response = _post(
        '{"redirect_uris": ["https://x/cb"]}',
        auth_header="Bearer sécret",
        initial_access_token="secret",
    )
    assert response.status_code == 401
