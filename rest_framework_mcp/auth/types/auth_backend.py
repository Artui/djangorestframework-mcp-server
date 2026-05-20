from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from rest_framework_mcp.auth.types.authorization_server_metadata import (
    AuthorizationServerMetadata,
)
from rest_framework_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata
from rest_framework_mcp.auth.types.token_info import TokenInfo


@runtime_checkable
class MCPAuthBackend(Protocol):
    """Pluggable authentication for the MCP transport.

    The transport calls :meth:`authenticate` on every request. A backend that
    returns ``None`` signals "no valid credentials" — the transport then emits
    a 401 with ``WWW-Authenticate`` built from
    :meth:`www_authenticate_challenge`. ``protected_resource_metadata`` powers
    the ``/.well-known/oauth-protected-resource`` view (RFC 9728) and returns
    a :class:`ProtectedResourceMetadata` dataclass; the contrib PRM ViewSet
    calls ``.to_dict()`` for the wire shape.

    :meth:`authorization_server_metadata` is consumed by the optional
    ``rest_framework_mcp.contrib.oauth`` mount (Phase 10d+) — backends
    that host an authorization server return an :class:`AuthorizationServerMetadata`
    dataclass; backends that don't host one raise :class:`NotImplementedError`
    so the contrib code can skip the AS endpoint matrix cleanly.

    Backends MUST be safe to instantiate without arguments — settings-driven
    configuration belongs inside the backend's own module.
    """

    def authenticate(self, request: HttpRequest) -> TokenInfo | None: ...

    def protected_resource_metadata(self) -> ProtectedResourceMetadata: ...

    def authorization_server_metadata(self) -> AuthorizationServerMetadata: ...

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str: ...


__all__ = ["MCPAuthBackend"]
