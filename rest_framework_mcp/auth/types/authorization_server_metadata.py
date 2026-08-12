from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    """RFC 8414 OAuth 2.0 Authorization Server Metadata payload.

    Returned by :meth:`MCPAuthBackend.authorization_server_metadata` and
    serialised by the contrib AS metadata ViewSet. A backend that hosts no
    authorization server raises :class:`NotImplementedError` instead, which
    that ViewSet maps to ``501 Not Implemented``.

    Field shapes mirror RFC 8414. The ``str``-typed endpoints default to ``""``
    so the wire shape is valid JSON even when the configuration is incomplete;
    populate ``SERVER_INFO`` to fill them.
    """

    issuer: str
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    registration_endpoint: str = ""
    grant_types_supported: list[str] = field(
        default_factory=lambda: ["authorization_code", "refresh_token"]
    )
    response_types_supported: list[str] = field(default_factory=lambda: ["code"])
    code_challenge_methods_supported: list[str] = field(default_factory=lambda: ["S256"])
    scopes_supported: list[str] = field(default_factory=list)
    token_endpoint_auth_methods_supported: list[str] = field(
        default_factory=lambda: ["client_secret_basic", "client_secret_post", "none"]
    )
    client_id_metadata_document_supported: bool = False
    """Whether the authorization server accepts an HTTPS URL as a ``client_id``.

    Clients check this to decide how to register: the priority order is
    pre-registration, then CIMD, then the deprecated Dynamic Client
    Registration, so a server that supports CIMD but stays silent sends every
    client down the deprecated path.

    Never hardcode this to ``True``. It describes the authorization server, not
    this package, so a backend must source it from what the AS actually does or
    the advertisement drifts from the behaviour."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "registration_endpoint": self.registration_endpoint,
            "grant_types_supported": list(self.grant_types_supported),
            "response_types_supported": list(self.response_types_supported),
            "code_challenge_methods_supported": list(self.code_challenge_methods_supported),
            "scopes_supported": list(self.scopes_supported),
            "token_endpoint_auth_methods_supported": list(
                self.token_endpoint_auth_methods_supported
            ),
            "client_id_metadata_document_supported": self.client_id_metadata_document_supported,
        }


__all__ = ["AuthorizationServerMetadata"]
