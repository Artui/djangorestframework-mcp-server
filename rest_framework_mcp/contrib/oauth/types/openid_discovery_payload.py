from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.auth.types.authorization_server_metadata import (
    AuthorizationServerMetadata,
)


@dataclass(frozen=True)
class OpenIDDiscoveryPayload:
    """OIDC discovery alias payload — extends :class:`AuthorizationServerMetadata`.

    Composes (not subclasses) the AS metadata so the underlying type
    stays exactly RFC 8414. The OIDC additions (``subject_types_supported``,
    ``id_token_signing_alg_values_supported``, ``response_modes_supported``)
    are advertised because some MCP / LLM-host clients probe
    ``/.well-known/openid-configuration`` first and skip the probe
    silently if these keys are absent.

    See :class:`OpenIDDiscoveryViewSet` for the rationale on returning
    OIDC-shaped metadata without implementing an actual ID-token
    endpoint.
    """

    base: AuthorizationServerMetadata
    subject_types_supported: list[str] = field(default_factory=lambda: ["public"])
    id_token_signing_alg_values_supported: list[str] = field(default_factory=lambda: ["RS256"])
    response_modes_supported: list[str] = field(default_factory=lambda: ["query"])

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = self.base.to_dict()
        out["subject_types_supported"] = list(self.subject_types_supported)
        out["id_token_signing_alg_values_supported"] = list(
            self.id_token_signing_alg_values_supported
        )
        out["response_modes_supported"] = list(self.response_modes_supported)
        return out


__all__ = ["OpenIDDiscoveryPayload"]
