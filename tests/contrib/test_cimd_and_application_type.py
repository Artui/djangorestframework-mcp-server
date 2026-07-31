"""CIMD advertisement, and the ``application_type`` clients are required to send.

Two halves of the same shift in the authorization spec: Client ID Metadata
Documents are now the preferred registration mechanism and Dynamic Client
Registration is deprecated. This server's job in that shift is small — say
truthfully whether the authorization server behind it accepts URL client ids,
and stop dropping the one DCR field the spec made mandatory for clients.
"""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)
from rest_framework_mcp.contrib.oauth.dcr_serializer import DynamicClientRegistrationSerializer

# ----- CIMD advertisement -----


def test_cimd_is_not_advertised_by_default() -> None:
    """DOT ships ``CIMD_ENABLED = False``, and this must mirror it, not assume."""
    metadata = DjangoOAuthToolkitBackend().authorization_server_metadata().to_dict()
    assert metadata["client_id_metadata_document_supported"] is False


def test_cimd_is_advertised_when_the_authorization_server_enables_it(monkeypatch) -> None:
    """Sourced from DOT, so the advertisement cannot drift from the behaviour.

    The setting is patched onto DOT's settings object rather than declared in
    ``OAUTH2_PROVIDER``, so the test asserts our behaviour on *both* sides of
    the version line: ``raising=False`` is what a DOT below 3.4 needs, and the
    lookup under test is the same ``getattr`` either way. Version-gating the
    test instead would have left the enabled branch unexercised on exactly the
    version the lockfile pins.
    """
    from oauth2_provider.settings import oauth2_settings

    monkeypatch.setattr(oauth2_settings, "CIMD_ENABLED", True, raising=False)
    metadata = DjangoOAuthToolkitBackend().authorization_server_metadata().to_dict()
    assert metadata["client_id_metadata_document_supported"] is True


# ----- application_type -----


def _validated(payload: dict[str, Any]) -> Any:
    serializer = DynamicClientRegistrationSerializer(
        data={"redirect_uris": ["https://example.test/cb"], **payload}
    )
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


@pytest.mark.django_db
@pytest.mark.parametrize("application_type", ["native", "web"])
def test_both_oidc_application_types_are_accepted(application_type: str) -> None:
    assert _validated({"application_type": application_type}).application_type == application_type


@pytest.mark.django_db
def test_an_unknown_application_type_is_rejected() -> None:
    serializer = DynamicClientRegistrationSerializer(
        data={"redirect_uris": ["https://example.test/cb"], "application_type": "desktop"}
    )
    assert not serializer.is_valid()
    assert "application_type" in serializer.errors


@pytest.mark.django_db
def test_application_type_is_optional() -> None:
    """A client that omits it still registers — the MUST is on the client."""
    assert _validated({}).application_type == ""


@pytest.mark.django_db
def test_a_native_client_may_use_a_localhost_redirect_uri() -> None:
    """And so may a ``web`` one: this server is not acting as an OIDC provider.

    OIDC would reject the second case. Enforcing it here would invent a
    restriction the authorization server underneath does not apply, which the
    spec explicitly permits non-OIDC servers to skip.
    """
    for application_type in ("native", "web"):
        serializer = DynamicClientRegistrationSerializer(
            data={
                "redirect_uris": ["http://localhost:3000/callback"],
                "application_type": application_type,
            }
        )
        assert serializer.is_valid(), serializer.errors
