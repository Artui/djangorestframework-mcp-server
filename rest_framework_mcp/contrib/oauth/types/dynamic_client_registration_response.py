from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DynamicClientRegistrationResponse:
    """RFC 7591 client information response.

    The wire shape
    [`DynamicClientRegistrationViewSet`][rest_framework_mcp.contrib.oauth.dynamic_client_registration_viewset.DynamicClientRegistrationViewSet]
    returns on a successful registration.

    RFC 7591 §3.2.1 lets the authorization server substitute any metadata value
    it likes but obliges it to return everything it registered, so every field
    here is the *resolved* value rather than an echo of the request: an
    untold substitution turns a legal downgrade into an undiagnosable failure,
    with the client behaving as what it asked to be while the token endpoint
    enforces something else.

    ``client_secret`` is the **plaintext** secret and is present only for
    confidential clients — DOT hashes the column on save, so this has to be the
    value generated before the ``Application`` was written; read back off the
    model it would be the PBKDF2 digest, which no client can authenticate with.
    A public client gets no secret at all, per RFC 7591 §2.
    ``client_secret_expires_at`` rides along whenever one is issued, §3.2.1
    making it REQUIRED in that case, with ``0`` meaning "does not expire".
    ``scope`` is emitted only when the request supplied one.
    """

    client_id: str
    client_id_issued_at: int
    client_name: str
    client_secret: str | None = None
    client_secret_expires_at: int = 0
    redirect_uris: list[str] = field(default_factory=list)
    grant_types: list[str] = field(default_factory=list)
    response_types: list[str] = field(default_factory=list)
    token_endpoint_auth_method: str = ""
    id_token_signed_response_alg: str = ""
    application_type: str = ""
    client_type: str = ""
    authorization_grant_type: str = ""
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "client_id": self.client_id,
            "client_id_issued_at": self.client_id_issued_at,
            "client_name": self.client_name,
            "redirect_uris": list(self.redirect_uris),
            "grant_types": list(self.grant_types),
            "response_types": list(self.response_types),
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
            "client_type": self.client_type,
            "authorization_grant_type": self.authorization_grant_type,
        }
        if self.application_type:
            # Echoed only when the client sent one: §3.2.1 asks for what was
            # *registered*, and echoing a default the client never chose would
            # assert a decision nobody made.
            out["application_type"] = self.application_type
        if self.id_token_signed_response_alg:
            # Omitted rather than empty when no signing algorithm was
            # registered: "" is not a value OIDC defines, and claiming one
            # would promise an ID token that cannot be minted.
            out["id_token_signed_response_alg"] = self.id_token_signed_response_alg
        if self.client_secret is not None:
            out["client_secret"] = self.client_secret
            out["client_secret_expires_at"] = self.client_secret_expires_at
        if self.scope is not None:
            out["scope"] = self.scope
        return out


__all__ = ["DynamicClientRegistrationResponse"]
