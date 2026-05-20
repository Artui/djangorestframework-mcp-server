from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    """RFC 9728 OAuth 2.0 Protected Resource Metadata payload.

    Returned by :meth:`MCPAuthBackend.protected_resource_metadata` and
    serialised onto the wire by the PRM ViewSet. Keys map 1:1 to the
    RFC 9728 field names; ``warning`` is the package-local extension
    that ``AllowAnyBackend`` uses to make dev-mode misconfiguration
    loud.
    """

    resource: str
    authorization_servers: list[str] = field(default_factory=list)
    bearer_methods_supported: list[str] = field(default_factory=lambda: ["header"])
    scopes_supported: list[str] = field(default_factory=list)
    resource_documentation: str | None = None
    # ``_warning`` is a package-local hint to make dev-mode backends
    # (``AllowAnyBackend``) detectable in client tooling. Not part of
    # RFC 9728.
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "resource": self.resource,
            "authorization_servers": self.authorization_servers,
            "bearer_methods_supported": self.bearer_methods_supported,
            "scopes_supported": self.scopes_supported,
        }
        if self.resource_documentation is not None:
            out["resource_documentation"] = self.resource_documentation
        if self.warning is not None:
            out["_warning"] = self.warning
        return out


__all__ = ["ProtectedResourceMetadata"]
