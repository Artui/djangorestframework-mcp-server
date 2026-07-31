from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

from rest_framework_mcp.auth.types.token_info import TokenInfo


class ScopeRequired:
    """Allow only requests whose token carries every listed OAuth scope.

    Takes a list, or a bare string for the single-scope case::

        ScopeRequired(["invoices:read", "invoices:write"])
        ScopeRequired("invoices:write")

    ⚠ **The bare string is not sugar — it closes a trap.** This used to accept
    only a list and normalise with ``list(scopes)``, which silently turns
    ``ScopeRequired("mcp:admin")`` into nine one-character scopes. Nothing
    failed at registration; the misconfiguration surfaced much later as a
    permission that could never be satisfied and a nonsense challenge
    (``scope="m c p : a d m i n"``). :class:`DjangoPermRequired` had always
    accepted a bare string, so the two siblings disagreed — and a developer who
    learned the permissive one naturally wrote the same thing here.
    """

    def __init__(self, scopes: str | list[str]) -> None:
        resolved: list[str] = [scopes] if isinstance(scopes, str) else list(scopes)
        if not resolved:
            # A ``ScopeRequired`` with nothing to require permits everything —
            # ``all(...)`` over an empty sequence is ``True`` — while *looking*
            # like a guard at the registration site, and while satisfying the
            # unguarded-tool check that would otherwise have warned. That is
            # the exact failure this class exists to prevent.
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
