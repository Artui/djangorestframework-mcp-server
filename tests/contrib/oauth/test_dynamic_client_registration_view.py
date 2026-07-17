"""OAuth DCR view behaviour: gating + happy path + errors."""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory

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
    response = _post(
        json.dumps({"redirect_uris": ["https://client.example/cb"], "scope": "mcp:read mcp:write"})
    )
    assert response.status_code == 201
    body = response.data
    assert body["scope"] == "mcp:read mcp:write"


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
