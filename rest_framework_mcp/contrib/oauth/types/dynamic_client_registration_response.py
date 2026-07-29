from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DynamicClientRegistrationResponse:
    """RFC 7591 client information response.

    The wire shape returned by :class:`DynamicClientRegistrationViewSet`
    on a successful registration.

    RFC 7591 §3.2.1 lets the authorization server substitute any metadata
    value it likes, but obliges it to return everything it registered. A
    substitution the client is never told about is what turns a legal
    downgrade into an undiagnosable failure: the client goes on behaving
    as what it asked to be while the token endpoint enforces something
    else. So every field here is the *resolved* value — what was written
    — not an echo of what was asked for.

    ``client_secret`` is the **plaintext** secret, and it is only present
    for confidential clients. DOT hashes the column on save, so the value
    here has to be the one generated before the ``Application`` was
    written — read it back off the model and you emit the PBKDF2 digest,
    which no client can authenticate with. A public client
    (``token_endpoint_auth_method: none``) gets no secret at all, per
    RFC 7591 §2. ``client_secret_expires_at`` rides along whenever a
    secret is issued because §3.2.1 makes it REQUIRED in that case; ``0``
    means "does not expire".

    ``scope`` is optional (only emitted when the request supplied one).
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
        if self.client_secret is not None:
            out["client_secret"] = self.client_secret
            out["client_secret_expires_at"] = self.client_secret_expires_at
        if self.scope is not None:
            out["scope"] = self.scope
        return out


__all__ = ["DynamicClientRegistrationResponse"]
