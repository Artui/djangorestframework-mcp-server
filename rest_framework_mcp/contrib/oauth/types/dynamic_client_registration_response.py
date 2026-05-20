from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DynamicClientRegistrationResponse:
    """RFC 7591 client information response.

    The wire shape returned by :class:`DynamicClientRegistrationViewSet`
    on a successful registration. ``scope`` is optional (only emitted
    when the request supplied one).
    """

    client_id: str
    client_secret: str
    client_id_issued_at: int
    client_name: str
    redirect_uris: list[str] = field(default_factory=list)
    client_type: str = ""
    authorization_grant_type: str = ""
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "client_id_issued_at": self.client_id_issued_at,
            "client_name": self.client_name,
            "redirect_uris": list(self.redirect_uris),
            "client_type": self.client_type,
            "authorization_grant_type": self.authorization_grant_type,
        }
        if self.scope is not None:
            out["scope"] = self.scope
        return out


__all__ = ["DynamicClientRegistrationResponse"]
