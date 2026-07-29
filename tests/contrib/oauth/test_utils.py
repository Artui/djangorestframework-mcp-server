"""ID-token algorithm capability resolution."""

from __future__ import annotations

import pytest
from oauth2_provider.models import Application

from rest_framework_mcp.contrib.oauth.utils import (
    HS256,
    NO_ALGORITHM,
    RS256,
    resolve_id_token_algorithm,
    supported_id_token_algorithms,
)


def test_algorithm_names_are_pinned_to_dots_own_constants() -> None:
    """Spelled literally so the module imports without the `[oauth]` extra.

    That trade only holds while the literals match, so pin them rather than
    letting a DOT rename resurface as a token-endpoint 500.
    """
    assert RS256 == Application.RS256_ALGORITHM
    assert HS256 == Application.HS256_ALGORITHM
    assert NO_ALGORITHM == Application.NO_ALGORITHM


@pytest.mark.parametrize("is_confidential", [True, False])
def test_omitted_takes_rs256_when_a_key_is_configured(is_confidential: bool) -> None:
    """RS256 works for public and confidential clients alike — it needs no secret."""
    algorithm, error = resolve_id_token_algorithm(
        "", is_confidential=is_confidential, rsa_key_configured=True
    )
    assert (algorithm, error) == (RS256, None)


@pytest.mark.parametrize("is_confidential", [True, False])
def test_omitted_registers_no_algorithm_without_a_key(is_confidential: bool) -> None:
    algorithm, error = resolve_id_token_algorithm(
        "", is_confidential=is_confidential, rsa_key_configured=False
    )
    assert (algorithm, error) == (NO_ALGORITHM, None)


def test_explicit_rs256_is_honoured_when_signable() -> None:
    assert resolve_id_token_algorithm(RS256, is_confidential=True, rsa_key_configured=True) == (
        RS256,
        None,
    )


def test_explicit_rs256_without_a_key_names_the_missing_setting() -> None:
    algorithm, error = resolve_id_token_algorithm(
        RS256, is_confidential=True, rsa_key_configured=False
    )
    assert algorithm == NO_ALGORITHM
    assert error is not None
    assert "OIDC_RSA_PRIVATE_KEY" in error


@pytest.mark.parametrize("is_confidential", [True, False])
def test_hs256_is_refused_for_every_client_type(is_confidential: bool) -> None:
    """Not a public-client limitation: the stored secret is a digest either way."""
    algorithm, error = resolve_id_token_algorithm(
        HS256, is_confidential=is_confidential, rsa_key_configured=True
    )
    assert algorithm == NO_ALGORITHM
    assert error is not None
    assert "hashed" in error


def test_supported_algorithms_follow_the_signing_key() -> None:
    assert supported_id_token_algorithms(rsa_key_configured=True) == [RS256]
    assert supported_id_token_algorithms(rsa_key_configured=False) == []
