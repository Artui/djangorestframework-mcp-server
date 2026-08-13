from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from rest_framework_mcp.auth.types.token_info import TokenInfo


@runtime_checkable
class MCPPermission(Protocol):
    """Per-tool / per-resource gate evaluated after authentication.

    The transport pulls authenticated state from the request as a
    [`TokenInfo`][rest_framework_mcp.auth.types.token_info.TokenInfo] and asks each
    permission whether the call may proceed. Returning ``False`` becomes a 403 +
    ``WWW-Authenticate``; raising lets the permission supply a richer payload via the
    JSON-RPC error path.

    Permissions MUST be cheap to construct — they are instantiated per binding
    at discovery time — and side-effect free at evaluation.
    """

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool: ...

    def required_scopes(self) -> list[str]:
        """Scopes to advertise in ``WWW-Authenticate`` when this permission denies.

        Implementations with no scope semantics return ``[]``.
        """
        ...


__all__ = ["MCPPermission"]
