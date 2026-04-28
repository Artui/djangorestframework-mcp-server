from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from django.http import HttpRequest

from rest_framework_mcp.auth.token_info import TokenInfo


@runtime_checkable
class MCPAuthBackend(Protocol):
    """Pluggable authentication for the MCP transport.

    The transport calls :meth:`authenticate` on every request. A backend that
    returns ``None`` signals "no valid credentials" — the transport then emits
    a 401 with ``WWW-Authenticate`` built from
    :meth:`www_authenticate_challenge`. ``protected_resource_metadata`` powers
    the ``/.well-known/oauth-protected-resource`` view (RFC 9728).

    Backends MUST be safe to instantiate without arguments — settings-driven
    configuration belongs inside the backend's own module.
    """

    def authenticate(self, request: HttpRequest) -> TokenInfo | None: ...

    def protected_resource_metadata(self) -> dict[str, Any]: ...

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str: ...


__all__ = ["MCPAuthBackend"]
