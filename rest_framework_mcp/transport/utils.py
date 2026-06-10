"""Shared helpers for the sync and async streamable-HTTP viewsets."""

from __future__ import annotations

from typing import Any

from rest_framework_mcp.auth.types.token_info import TokenInfo


def principal_for_token(token: TokenInfo) -> str:
    """Derive the stable principal id a session is bound to.

    Sessions minted at ``initialize`` are owned by the authenticated
    principal; every subsequent request must present the same principal or
    the session is treated as unknown (404 — deliberately indistinguishable
    from a non-existent session so ownership probing yields no oracle).

    The id is the resolved user's primary key. Unauthenticated principals
    (an ``AnonymousUser`` from a permissive backend such as
    ``AllowAnyBackend``) all map to the shared ``"anonymous"`` principal —
    session binding is only as strong as the auth backend behind it.
    """
    user: Any = token.user
    pk: Any = getattr(user, "pk", None)
    if pk is not None:
        return f"user:{pk}"
    return "anonymous"


__all__ = ["principal_for_token"]
