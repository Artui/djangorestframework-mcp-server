"""The leg the DCR view tests never reached: can a registered client authenticate?

``/oauth/register/`` returning ``201`` with a well-formed body says nothing about
whether the credentials in that body work. Reachability and usability diverge at
the token endpoint, so these tests drive DOT's own token view with exactly what
DCR handed out — a public client authenticating with PKCE alone, and a
confidential client authenticating with the issued secret.

The authorize leg is stood in for by writing the ``Grant`` row directly, which is
what DOT persists once the user has logged in and consented. That keeps the test
free of session/login middleware while still exercising the two things that were
broken: client authentication, and the client type the registration produced.
"""

from __future__ import annotations

import base64
import hashlib
import json
import types
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import include, path
from django.utils import timezone

from rest_framework_mcp.contrib.oauth.dynamic_client_registration_viewset import (
    DynamicClientRegistrationViewSet,
)

REDIRECT_URI = "https://client.example/cb"
CODE_VERIFIER = "a" * 64
CODE_CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(CODE_VERIFIER.encode()).digest()).decode().rstrip("=")
)


def _oauth_urlconf() -> types.ModuleType:
    """Throwaway URL conf mounting DOT's own authorization-server endpoints."""
    module = types.ModuleType("tests.contrib.oauth._dot_urlconf")
    module.urlpatterns = [path("o/", include("oauth2_provider.urls"))]  # type: ignore[attr-defined]
    return module


def _register(**extra: Any) -> dict[str, Any]:
    """Register a client through the DCR view and return the response body."""
    from django.test import RequestFactory

    payload: dict[str, Any] = {
        "redirect_uris": [REDIRECT_URI],
        "client_name": "Probe client",
        **extra,
    }
    request = RequestFactory().post(
        "/oauth/register/",
        data=json.dumps(payload).encode(),
        content_type="application/json",
    )
    view = DynamicClientRegistrationViewSet.as_view(
        {"post": "create"}, dcr_enabled=True, initial_access_token=None
    )
    response = view(request)
    assert response.status_code == 201, response.data
    return dict(response.data)


def _issue_grant(client_id: str, *, scope: str = "") -> str:
    """Stand in for the authorize leg: persist the code DOT would have written."""
    from oauth2_provider.models import Application, Grant

    # ``last_login`` is set because the real authorize leg cannot be reached
    # without one, and DOT's ``get_id_token_dictionary`` reads it for the
    # ``auth_time`` claim — a stand-in without it is not the state a consented
    # grant is ever in.
    user = get_user_model().objects.create_user(username="probe", last_login=timezone.now())
    application = Application.objects.get(client_id=client_id)
    grant = Grant.objects.create(
        application=application,
        user=user,
        code="probe-authorization-code",
        expires=timezone.now() + timedelta(minutes=10),
        redirect_uri=REDIRECT_URI,
        scope=scope,
        code_challenge=CODE_CHALLENGE,
        code_challenge_method="S256",
    )
    return grant.code


def _exchange(body: dict[str, str], *, basic_auth: tuple[str, str] | None = None) -> Any:
    headers: dict[str, str] = {}
    if basic_auth is not None:
        raw = f"{basic_auth[0]}:{basic_auth[1]}".encode()
        headers["HTTP_AUTHORIZATION"] = f"Basic {base64.b64encode(raw).decode()}"
    with override_settings(ROOT_URLCONF=_oauth_urlconf()):
        return Client().post("/o/token/", data=body, **headers)


@pytest.mark.django_db
def test_public_registration_can_exchange_its_code_with_pkce_alone() -> None:
    """The Claude-Web shape: ``token_endpoint_auth_method: none``, no secret.

    Before the fix this registration was silently downgraded to a confidential
    client, so the exchange died at client authentication with
    ``401 invalid_client`` — after a clean login and consent.
    """
    registration = _register(
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    assert registration["client_type"] == "public"
    assert registration["token_endpoint_auth_method"] == "none"
    assert registration["grant_types"] == ["authorization_code", "refresh_token"]
    assert registration["response_types"] == ["code"]
    assert "client_secret" not in registration

    code = _issue_grant(registration["client_id"])
    response = _exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": registration["client_id"],
            "code_verifier": CODE_VERIFIER,
        }
    )

    assert response.status_code == 200, response.content
    assert json.loads(response.content)["access_token"]


@pytest.mark.django_db
def test_confidential_registration_can_authenticate_with_the_issued_secret() -> None:
    """The returned ``client_secret`` is the plaintext, not the stored digest.

    Before the fix this was the PBKDF2 hash of a secret that was never emitted,
    so the only credential DCR ever handed out could not verify against itself.
    """
    registration = _register()
    assert registration["token_endpoint_auth_method"] == "client_secret_basic"
    assert not registration["client_secret"].startswith("pbkdf2_")
    assert registration["client_secret_expires_at"] == 0

    code = _issue_grant(registration["client_id"])
    response = _exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": CODE_VERIFIER,
        },
        basic_auth=(registration["client_id"], registration["client_secret"]),
    )

    assert response.status_code == 200, response.content
    assert json.loads(response.content)["access_token"]


@pytest.mark.django_db
def test_issued_secret_verifies_against_the_stored_hash() -> None:
    """Narrower than the exchange above, and it fails first when the fix regresses."""
    from django.contrib.auth.hashers import check_password
    from oauth2_provider.models import Application

    registration = _register()
    application = Application.objects.get(client_id=registration["client_id"])

    assert application.client_secret.startswith("pbkdf2_")  # DOT hashed the column
    assert check_password(registration["client_secret"], application.client_secret)


@pytest.mark.django_db
def test_public_registration_stores_a_secret_nobody_was_given() -> None:
    """A public client's row must not be authenticable against a known value.

    Letting the column default to blank would leave DOT holding a hash of the
    empty string, which ``_check_secret("")`` happily verifies.
    """
    from django.contrib.auth.hashers import check_password
    from oauth2_provider.models import Application

    registration = _register(token_endpoint_auth_method="none")
    application = Application.objects.get(client_id=registration["client_id"])

    assert application.client_secret
    assert not check_password("", application.client_secret)
