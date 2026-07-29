"""Normalisation between the RFC 7591 and DOT spellings of a registration."""

from __future__ import annotations

from typing import Any

import pytest
from oauth2_provider.models import Application

from rest_framework_mcp.contrib.oauth.dcr_serializer import (
    _AUTH_METHOD_CLIENT_TYPES,
    _CLIENT_TYPE_AUTH_METHODS,
    _GRANT_TYPE_ALIASES,
    DynamicClientRegistrationSerializer,
)
from rest_framework_mcp.contrib.oauth.types.dynamic_client_registration_request import (
    DynamicClientRegistrationRequest,
)


def _validate(**payload: Any) -> DynamicClientRegistrationRequest:
    serializer = DynamicClientRegistrationSerializer(
        data={"redirect_uris": ["https://client.example/cb"], **payload}
    )
    assert serializer.is_valid(), serializer.errors
    return serializer.save()


def _errors(**payload: Any) -> dict[str, Any]:
    serializer = DynamicClientRegistrationSerializer(
        data={"redirect_uris": ["https://client.example/cb"], **payload}
    )
    assert not serializer.is_valid()
    return dict(serializer.errors)


def test_mappings_are_pinned_to_dots_own_constants() -> None:
    """The mappings spell DOT's values literally so this module imports without DOT.

    That trade is only safe while the literals still match, so pin them here
    rather than letting a DOT rename surface as an unusable registration.
    """
    assert set(_AUTH_METHOD_CLIENT_TYPES.values()) == {
        Application.CLIENT_CONFIDENTIAL,
        Application.CLIENT_PUBLIC,
    }
    assert set(_CLIENT_TYPE_AUTH_METHODS) == {
        Application.CLIENT_CONFIDENTIAL,
        Application.CLIENT_PUBLIC,
    }
    assert _GRANT_TYPE_ALIASES == {
        "authorization_code": Application.GRANT_AUTHORIZATION_CODE,
        "client_credentials": Application.GRANT_CLIENT_CREDENTIALS,
        "implicit": Application.GRANT_IMPLICIT,
        "password": Application.GRANT_PASSWORD,
    }


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("none", Application.CLIENT_PUBLIC),
        ("client_secret_basic", Application.CLIENT_CONFIDENTIAL),
        ("client_secret_post", Application.CLIENT_CONFIDENTIAL),
    ],
)
def test_token_endpoint_auth_method_drives_client_type(method: str, expected: str) -> None:
    instance = _validate(token_endpoint_auth_method=method)
    assert instance.client_type == expected
    assert instance.token_endpoint_auth_method == method


def test_omitting_both_spellings_defaults_to_confidential_authorization_code() -> None:
    instance = _validate()
    assert instance.client_type == Application.CLIENT_CONFIDENTIAL
    assert instance.token_endpoint_auth_method == "client_secret_basic"
    assert instance.authorization_grant_type == Application.GRANT_AUTHORIZATION_CODE
    assert instance.grant_types == ["authorization_code"]


def test_dot_spelling_is_echoed_back_in_rfc_terms() -> None:
    """A caller who used DOT's escape hatch still gets a spec-shaped response."""
    instance = _validate(
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
    )
    assert instance.token_endpoint_auth_method == "none"
    assert instance.grant_types == ["client_credentials"]


def test_refresh_token_is_ignored_when_resolving_the_primary_grant() -> None:
    instance = _validate(grant_types=["authorization_code", "refresh_token"])
    assert instance.authorization_grant_type == Application.GRANT_AUTHORIZATION_CODE
    # The client's own list is echoed verbatim — DOT does issue refresh tokens
    # for this grant, so trimming it would understate what was registered.
    assert instance.grant_types == ["authorization_code", "refresh_token"]


def test_refresh_token_alone_is_rejected() -> None:
    assert "grant_types" in _errors(grant_types=["refresh_token"])


def test_two_primary_grants_are_rejected() -> None:
    """DOT models one grant per application; picking one silently is how this bug
    class started."""
    errors = _errors(grant_types=["authorization_code", "client_credentials"])
    assert "grant_types" in errors


def test_agreeing_spellings_are_accepted() -> None:
    instance = _validate(
        token_endpoint_auth_method="none",
        client_type=Application.CLIENT_PUBLIC,
        grant_types=["authorization_code"],
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    assert instance.client_type == Application.CLIENT_PUBLIC
    assert instance.authorization_grant_type == Application.GRANT_AUTHORIZATION_CODE


def test_contradicting_client_type_is_rejected() -> None:
    errors = _errors(token_endpoint_auth_method="none", client_type=Application.CLIENT_CONFIDENTIAL)
    assert "client_type" in errors


def test_contradicting_grant_type_is_rejected() -> None:
    errors = _errors(
        grant_types=["client_credentials"],
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    assert "authorization_grant_type" in errors


def test_unknown_auth_method_is_rejected_per_field() -> None:
    assert "token_endpoint_auth_method" in _errors(token_endpoint_auth_method="private_key_jwt")


def test_unknown_grant_type_is_rejected_per_field() -> None:
    assert "grant_types" in _errors(grant_types=["urn:ietf:params:oauth:grant-type:device_code"])


def test_unmodelled_rfc_fields_are_ignored() -> None:
    """RFC 7591 §2 requires ignoring metadata the server doesn't understand."""
    instance = _validate(contacts=["ops@client.example"], logo_uri="https://client.example/logo")
    assert not hasattr(instance, "contacts")


@pytest.mark.parametrize(
    ("grant", "expected"),
    [
        (Application.GRANT_AUTHORIZATION_CODE, ["code"]),
        (Application.GRANT_IMPLICIT, ["token"]),
        (Application.GRANT_CLIENT_CREDENTIALS, []),
        (Application.GRANT_PASSWORD, []),
    ],
)
def test_response_types_are_derived_from_the_grant(grant: str, expected: list[str]) -> None:
    """RFC 7591 §2.1 makes response_types a function of the grant, not a free choice."""
    assert _validate(authorization_grant_type=grant).response_types == expected


def test_matching_response_types_are_accepted() -> None:
    instance = _validate(
        grant_types=["authorization_code", "refresh_token"], response_types=["code"]
    )
    assert instance.response_types == ["code"]


def test_response_types_inconsistent_with_the_grant_are_rejected() -> None:
    errors = _errors(grant_types=["client_credentials"], response_types=["code"])
    assert "response_types" in errors


def test_unknown_response_type_is_rejected_per_field() -> None:
    assert "response_types" in _errors(response_types=["id_token"])
