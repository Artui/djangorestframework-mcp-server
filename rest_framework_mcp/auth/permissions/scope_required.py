from __future__ import annotations

from django.http import HttpRequest

from rest_framework_mcp.auth.token_info import TokenInfo


class ScopeRequired:
    """Allow only requests whose token carries every listed OAuth scope.

    Constructor takes the scopes positionally so usage stays compact:
    ``ScopeRequired(["invoices:write"])``.
    """

    def __init__(self, scopes: list[str]) -> None:
        self._scopes: list[str] = list(scopes)

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return token.has_all_scopes(self._scopes)

    def required_scopes(self) -> list[str]:
        return list(self._scopes)


__all__ = ["ScopeRequired"]
