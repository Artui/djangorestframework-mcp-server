from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

from rest_framework_mcp.auth.types.token_info import TokenInfo


class ScopeRequired:
    """Allow only requests whose token carries every listed OAuth scope.

    Takes a list, or a bare string for the single-scope case::

        ScopeRequired(["invoices:read", "invoices:write"])
        ScopeRequired("invoices:write")

    **The bare string is not sugar — it closes a trap.** Normalising with
    ``list(scopes)`` would silently turn ``ScopeRequired("mcp:admin")`` into
    nine one-character scopes: nothing fails at registration, and the
    misconfiguration surfaces much later as a permission that can never be
    satisfied and a nonsense challenge. :class:`DjangoPermRequired` takes a
    bare string too, so the siblings agree.
    """

    def __init__(self, scopes: str | list[str]) -> None:
        resolved: list[str] = [scopes] if isinstance(scopes, str) else list(scopes)
        if not resolved:
            # ``all(...)`` over an empty sequence is ``True``, so an empty
            # ``ScopeRequired`` permits everything while reading as a guard at
            # the registration site — and while satisfying the unguarded-tool
            # check that would otherwise have warned.
            raise ImproperlyConfigured(
                "ScopeRequired() needs at least one scope: an empty one permits "
                "every request while reading as a guard. Pass e.g. "
                'ScopeRequired("mcp:admin").'
            )
        self._scopes: list[str] = resolved

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return token.has_all_scopes(self._scopes)

    def required_scopes(self) -> list[str]:
        return list(self._scopes)


__all__ = ["ScopeRequired"]
