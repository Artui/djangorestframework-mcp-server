"""Normalisation between the RFC 7591 and DOT spellings of a registration."""

from __future__ import annotations

from typing import Any

import pytest
from oauth2_provider.models import Application

from rest_framework_mcp.auth.types.authorization_server_metadata import (
    AuthorizationServerMetadata,
)
from rest_framework_mcp.contrib.oauth.dcr_serializer import (
    _AUTH_METHOD_CLIENT_TYPES,
    _CLIENT_TYPE_AUTH_METHODS,
    _GRANT_TYPE_ALIASES,
    _REGISTERABLE_GRANT_TYPES,
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
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    assert instance.token_endpoint_auth_method == "none"
    assert instance.grant_types == ["authorization_code"]


def test_the_dot_spelling_cannot_register_a_grant_the_rfc_spelling_refuses() -> None:
    """The escape hatch is a second vocabulary, not a second door."""
    assert "authorization_grant_type" in _errors(
        authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS
    )


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
        grant_types=["implicit"],
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    assert "grant_types" in errors


def test_unknown_auth_method_is_rejected_per_field() -> None:
    assert "token_endpoint_auth_method" in _errors(token_endpoint_auth_method="private_key_jwt")


def test_unknown_grant_type_is_rejected_per_field() -> None:
    assert "grant_types" in _errors(grant_types=["urn:ietf:params:oauth:grant-type:device_code"])


def test_unmodelled_rfc_fields_are_ignored() -> None:
    """RFC 7591 §2 requires ignoring metadata the server doesn't understand."""
    instance = _validate(contacts=["ops@client.example"], logo_uri="https://client.example/logo")
    assert not hasattr(instance, "contacts")


def test_response_types_are_derived_from_the_grant() -> None:
    """RFC 7591 §2.1 makes response_types a function of the grant, not a free choice."""
    instance = _validate(authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE)
    assert instance.response_types == ["code"]


def test_matching_response_types_are_accepted() -> None:
    instance = _validate(
        grant_types=["authorization_code", "refresh_token"], response_types=["code"]
    )
    assert instance.response_types == ["code"]


def test_response_types_inconsistent_with_the_grant_are_rejected() -> None:
    errors = _errors(grant_types=["authorization_code"], response_types=["token"])
    assert "response_types" in errors


def test_unknown_response_type_is_rejected_per_field() -> None:
    assert "response_types" in _errors(response_types=["id_token"])


# ---------- what a dynamically registered client is allowed to hold ----------


@pytest.mark.parametrize("grant", ["client_credentials", "password", "implicit"])
def test_a_grant_that_needs_no_user_cannot_be_registered_dynamically(grant: str) -> None:
    """A dynamic registration has no owning user, and the escalation runs to the end.

    The row created here is ownerless, and the initial-access-token gate is
    skipped entirely when no token is configured — the default. A
    client-credentials token minted against such a client carries no user at
    all, and the scope permission tests only the token's scopes, so it satisfies
    every scope-gated tool. That makes an open registration endpoint an
    unauthenticated path onto the whole tool surface.
    """
    errors = _errors(grant_types=[grant])
    assert "grant_types" in errors
    assert "no owning user" in str(errors["grant_types"])


def test_the_registerable_grant_matches_what_the_server_advertises() -> None:
    """Registration must not accept a grant the metadata document never offered."""
    metadata = AuthorizationServerMetadata(issuer="https://as.example")
    assert set(_REGISTERABLE_GRANT_TYPES) <= set(metadata.grant_types_supported)


def test_refresh_token_still_rides_along_with_the_registerable_grant() -> None:
    instance = _validate(grant_types=["authorization_code", "refresh_token"])
    assert instance.authorization_grant_type == Application.GRANT_AUTHORIZATION_CODE


# ---------- redirect_uris: absolute URIs, on the schemes the AS will honour ----------


def test_a_native_clients_private_use_scheme_registers_where_the_server_allows_it(
    settings,
) -> None:
    """RFC 8252 §7.1 is the case ``application_type: native`` exists to describe.

    DRF's ``URLField`` allowlisted the http family, so the endpoint answered 400
    before DOT ever saw the registration — even on a deployment that had widened
    DOT's own scheme list precisely so those clients could redirect.
    """
    settings.OAUTH2_PROVIDER = {
        **getattr(settings, "OAUTH2_PROVIDER", {}),
        "ALLOWED_REDIRECT_URI_SCHEMES": ["https", "com.example.app"],
    }
    instance = _validate(
        redirect_uris=["com.example.app:/oauth2redirect"], application_type="native"
    )
    assert instance.redirect_uris == ["com.example.app:/oauth2redirect"]


def test_a_scheme_the_authorization_server_will_not_redirect_to_is_refused() -> None:
    errors = _errors(redirect_uris=["com.example.app:/oauth2redirect"])
    assert "redirect_uris" in errors
    assert "ALLOWED_REDIRECT_URI_SCHEMES" in str(errors["redirect_uris"])


@pytest.mark.parametrize("uri", ["/oauth2redirect", "client.example/cb", "https:", ""])
def test_a_relative_or_empty_redirect_uri_is_refused(uri: str) -> None:
    assert "redirect_uris" in _errors(redirect_uris=[uri])


def test_an_unparseable_redirect_uri_is_refused() -> None:
    # ``urlsplit`` raises on an unclosed IPv6 literal; unhandled that is a 500.
    errors = _errors(redirect_uris=["http://[::1"])
    assert "redirect_uris" in errors


def test_ordinary_https_redirect_uris_are_unaffected() -> None:
    instance = _validate(redirect_uris=["https://client.example/cb"])
    assert instance.redirect_uris == ["https://client.example/cb"]
