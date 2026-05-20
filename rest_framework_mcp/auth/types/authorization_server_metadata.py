from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    """RFC 8414 OAuth 2.0 Authorization Server Metadata payload.

    Returned by :meth:`MCPAuthBackend.authorization_server_metadata`
    and serialised by the contrib AS metadata ViewSet. Backends that
    don't host an AS raise :class:`NotImplementedError` instead of
    returning this dataclass; the calling ViewSet maps the exception
    to ``501 Not Implemented``.

    Field shapes mirror RFC 8414; ``str``-typed endpoints default to
    ``""`` so the wire shape is always valid JSON even when the
    configuration is incomplete (callers can populate ``SERVER_INFO``
    to fill them).
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
        }


__all__ = ["AuthorizationServerMetadata"]
