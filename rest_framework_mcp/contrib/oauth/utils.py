"""Internal helpers shared by the contrib OAuth viewsets."""

from __future__ import annotations

# The scope whose presence makes an ID token, and therefore a signing
# algorithm, mandatory rather than optional.
OPENID_SCOPE = "openid"

# DOT's ``Application.RS256_ALGORITHM`` / ``HS256_ALGORITHM`` / ``NO_ALGORITHM``,
# spelled literally so this module stays importable without the ``[oauth]``
# extra, and pinned against ``Application`` by the tests. They double as OIDC's
# ``id_token_signed_response_alg`` values, so no translation is needed.
RS256 = "RS256"
HS256 = "HS256"
NO_ALGORITHM = ""

_HASHED_SECRET_REASON = (
    "This server stores client secrets hashed, so HS256 — which signs the ID "
    "token with the client secret itself — would sign with the digest and "
    "produce a token the client cannot verify. Request RS256, or omit the "
    "field."
)
_NO_KEY_REASON = (
    "RS256 needs an RSA signing key, and OAUTH2_PROVIDER['OIDC_RSA_PRIVATE_KEY'] "
    "is not set on this server. Omit the field to register without ID-token "
    "support, or ask the operator to configure a key."
)


def resolve_id_token_algorithm(
    requested: str, *, is_confidential: bool, rsa_key_configured: bool
) -> tuple[str, str | None]:
    """Resolve an RFC 7591 ``id_token_signed_response_alg`` to DOT's ``algorithm``.

    Returns ``(algorithm, error)``. A non-``None`` ``error`` means the
    registration cannot be honoured and must be refused with
    ``invalid_client_metadata`` — which is the point of resolving it at
    registration rather than at the token endpoint, where DOT raises
    ``ImproperlyConfigured`` and the client gets an unactionable 500.

    **HS256 is never available here**, whatever the client type. DOT's
    ``jwk_key`` builds the HS256 key from ``Application.client_secret``, and
    this endpoint leaves ``hash_client_secret`` at its default, so that column
    holds a PBKDF2 digest rather than the secret the client was handed. Signing
    with the digest yields a signature that can never verify. ``is_confidential``
    is still taken so the reason stays accurate if hashing ever becomes
    configurable; a public client has no secret to sign with either way.

    An omitted value takes RS256 when the server can sign with it, and
    otherwise registers no algorithm — the honest outcome for a deployment that
    is not doing OIDC.
    """
    if requested == HS256:
        return NO_ALGORITHM, _HASHED_SECRET_REASON
    if requested == RS256:
        if not rsa_key_configured:
            return NO_ALGORITHM, _NO_KEY_REASON
        return RS256, None
    return (RS256 if rsa_key_configured else NO_ALGORITHM), None


def supported_id_token_algorithms(*, rsa_key_configured: bool) -> list[str]:
    """The algorithms this server can actually sign an ID token with.

    Drives ``id_token_signing_alg_values_supported`` in the OIDC discovery
    payload. Derived rather than hardcoded: advertising an algorithm the
    registration endpoint cannot provision is what turns a configuration gap
    into a token-endpoint 500. HS256 is absent for the reason given in
    ``resolve_id_token_algorithm``, and the list is empty rather than
    falsely populated on a server with no RSA key.
    """
    return [RS256] if rsa_key_configured else []


__all__ = ["OPENID_SCOPE", "resolve_id_token_algorithm", "supported_id_token_algorithms"]
