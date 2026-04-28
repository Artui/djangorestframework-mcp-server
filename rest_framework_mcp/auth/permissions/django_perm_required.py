from __future__ import annotations

from django.http import HttpRequest

from rest_framework_mcp.auth.token_info import TokenInfo


class DjangoPermRequired:
    """Allow only requests whose user has the given Django permission(s).

    Wraps ``user.has_perm`` from the standard Django auth backend. A token
    backed by ``AnonymousUser`` will always be rejected — that is the point
    of using this class instead of :class:`ScopeRequired`.
    """

    def __init__(self, perm: str | list[str]) -> None:
        self._perms: list[str] = [perm] if isinstance(perm, str) else list(perm)

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        user = token.user
        if not getattr(user, "is_authenticated", False):
            return False
        return all(user.has_perm(p) for p in self._perms)

    def required_scopes(self) -> list[str]:
        return []


__all__ = ["DjangoPermRequired"]
