from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

from rest_framework_mcp.auth.types.token_info import TokenInfo


class DjangoPermRequired:
    """Allow only requests whose user has the given Django permission(s).

    Wraps ``user.has_perm`` from the standard Django auth backend. A token
    backed by ``AnonymousUser`` is always rejected — that is the point of using
    this class instead of :class:`ScopeRequired`. Takes a list or a bare
    string, like :class:`ScopeRequired`.
    """

    def __init__(self, perm: str | list[str]) -> None:
        resolved: list[str] = [perm] if isinstance(perm, str) else list(perm)
        if not resolved:
            # Same trap as an empty ``ScopeRequired``: ``all(...)`` over an
            # empty sequence is ``True``, so this would let every authenticated
            # user through while reading as a guard, and while satisfying the
            # unguarded-tool check that would otherwise warn.
            raise ImproperlyConfigured(
                "DjangoPermRequired() needs at least one permission: an empty one "
                "allows every authenticated user while reading as a guard. Pass "
                'e.g. DjangoPermRequired("invoices.refund_invoice").'
            )
        self._perms: list[str] = resolved

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        user = token.user
        if not getattr(user, "is_authenticated", False):
            return False
        return all(user.has_perm(p) for p in self._perms)

    def required_scopes(self) -> list[str]:
        return []


__all__ = ["DjangoPermRequired"]
