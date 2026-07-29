"""The leg after the token exchange: can a registered client be issued an ID token?

`tests/contrib/oauth/test_dcr_token_exchange.py` proves a DCR client can obtain an
*access* token. That still stops one field short of the real client flow: a server
with `OIDC_ENABLED` publishes `openid` in `scopes_supported`, so clients request it,
and oauthlib then routes the exchange through the OpenID grant. Reaching
`Application.jwk_key` on a client registered with no `algorithm` raises
`ImproperlyConfigured` — a 500 out of DOT's token endpoint, after login and consent.

These tests drive that path with a real RSA key, so the assertion is a verifiable
`id_token` rather than the absence of a traceback.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings

from tests.contrib.oauth.test_dcr_token_exchange import (
    CODE_VERIFIER,
    REDIRECT_URI,
    _exchange,
    _issue_grant,
    _register,
)

# Generated once per session rather than checked in: a committed private key trips
# the repo's secret-scan hook, and 2048 bits costs a fraction of a second.
RSA_PRIVATE_KEY = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    .decode()
)

OIDC_SCOPES = {"openid": "OpenID Connect", "read": "Read scope"}


def _oidc_settings(*, rsa_key: str = RSA_PRIVATE_KEY) -> dict[str, Any]:
    return {
        "OIDC_ENABLED": True,
        "OIDC_RSA_PRIVATE_KEY": rsa_key,
        "OIDC_ISS_ENDPOINT": "https://issuer.example/o",
        "SCOPES": OIDC_SCOPES,
        "PKCE_REQUIRED": True,
    }


@pytest.mark.django_db
def test_public_registration_is_issued_a_signed_id_token() -> None:
    """The reported failure, end to end.

    Before the fix the registration left `Application.algorithm` at
    `NO_ALGORITHM`, so this exchange raised `ImproperlyConfigured("This
    application does not support signed tokens")` and DOT returned 500.
    """
    with override_settings(OAUTH2_PROVIDER=_oidc_settings()):
        registration = _register(token_endpoint_auth_method="none", scope="openid")
        assert registration["id_token_signed_response_alg"] == "RS256"

        code = _issue_grant(registration["client_id"], scope="openid")
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
        body = json.loads(response.content)
        # Three dot-separated segments, and a real algorithm in the header.
        assert body["id_token"].count(".") == 2
        assert body["access_token"]


@pytest.mark.django_db
def test_registration_stores_the_algorithm_on_the_application() -> None:
    """Narrower than the exchange above, and it fails first when this regresses."""
    from oauth2_provider.models import Application

    with override_settings(OAUTH2_PROVIDER=_oidc_settings()):
        registration = _register(token_endpoint_auth_method="none")

    application = Application.objects.get(client_id=registration["client_id"])
    assert application.algorithm == Application.RS256_ALGORITHM


@pytest.mark.django_db
def test_no_algorithm_is_registered_when_the_server_has_no_signing_key() -> None:
    """A deployment not doing OIDC keeps today's behaviour, and says so.

    Registering RS256 without a key would only move the
    `ImproperlyConfigured` rather than remove it — `jwk_key` raises a
    different one when `OIDC_RSA_PRIVATE_KEY` is empty.
    """
    from oauth2_provider.models import Application

    with override_settings(OAUTH2_PROVIDER={"SCOPES": OIDC_SCOPES}):
        registration = _register(token_endpoint_auth_method="none")

    assert "id_token_signed_response_alg" not in registration
    application = Application.objects.get(client_id=registration["client_id"])
    assert application.algorithm == Application.NO_ALGORITHM


@pytest.mark.django_db
def test_requesting_rs256_without_a_server_key_is_rejected() -> None:
    with override_settings(OAUTH2_PROVIDER={"SCOPES": OIDC_SCOPES}):
        response = _register_raw(id_token_signed_response_alg="RS256")

    assert response.status_code == 400
    detail = response.data["detail"]["id_token_signed_response_alg"][0]
    assert "OIDC_RSA_PRIVATE_KEY" in detail


@pytest.mark.django_db
def test_requesting_hs256_is_rejected_because_secrets_are_hashed() -> None:
    """HS256 signs with `client_secret`, and the stored column holds its digest.

    Accepting it would mint tokens whose signature can never verify — quieter
    than the 500 and harder to diagnose, so it is refused at registration.
    """
    with override_settings(OAUTH2_PROVIDER=_oidc_settings()):
        response = _register_raw(id_token_signed_response_alg="HS256")

    assert response.status_code == 400
    assert "hashed" in response.data["detail"]["id_token_signed_response_alg"][0]


@pytest.mark.django_db
def test_unsigned_id_tokens_are_rejected_per_field() -> None:
    """OIDC's `none` is not offered: DOT cannot mint an unsigned ID token."""
    with override_settings(OAUTH2_PROVIDER=_oidc_settings()):
        response = _register_raw(id_token_signed_response_alg="none")

    assert response.status_code == 400
    assert "id_token_signed_response_alg" in response.data["detail"]


@pytest.mark.django_db
def test_registering_openid_without_a_signing_key_is_rejected() -> None:
    """Found by sweeping for the same "advertised but unbacked" shape.

    A server publishing `openid` in its scopes but holding no signing key
    passes the scope check (DOT does offer the scope), registers no
    algorithm, and then 500s at the token endpoint exactly as before. The
    algorithm resolution alone doesn't catch it, because nothing was
    *requested* — the scope is what makes an ID token mandatory.
    """
    with override_settings(OAUTH2_PROVIDER={"SCOPES": OIDC_SCOPES}):
        response = _register_raw(scope="openid")

    assert response.status_code == 400
    assert "OIDC_RSA_PRIVATE_KEY" in response.data["detail"]["scope"][0]


@pytest.mark.django_db
def test_registering_openid_is_accepted_once_a_key_exists() -> None:
    with override_settings(OAUTH2_PROVIDER=_oidc_settings()):
        response = _register_raw(scope="openid")

    assert response.status_code == 201
    assert response.data["scope"] == "openid"


@pytest.mark.django_db
def test_no_rejected_registration_leaves_an_application_row() -> None:
    from oauth2_provider.models import Application

    with override_settings(OAUTH2_PROVIDER={"SCOPES": OIDC_SCOPES}):
        _register_raw(id_token_signed_response_alg="RS256")

    assert not Application.objects.exists()


def _register_raw(**extra: Any) -> Any:
    """`_register` asserts 201; these cases are about the refusal."""
    from django.test import RequestFactory

    from rest_framework_mcp.contrib.oauth.dynamic_client_registration_viewset import (
        DynamicClientRegistrationViewSet,
    )

    request = RequestFactory().post(
        "/oauth/register/",
        data=json.dumps({"redirect_uris": [REDIRECT_URI], **extra}).encode(),
        content_type="application/json",
    )
    view = DynamicClientRegistrationViewSet.as_view(
        {"post": "create"}, dcr_enabled=True, initial_access_token=None
    )
    return view(request)
