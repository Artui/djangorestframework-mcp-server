from __future__ import annotations

from typing import Any

from rest_framework_mcp.auth.types.token_info import TokenInfo


def principal_for_token(token: TokenInfo) -> str:
    """Derive the stable principal id that owns a session or a task.

    Sessions minted at ``initialize`` are owned by the authenticated principal;
    every subsequent request must present the same one or the session is
    treated as unknown (404 — deliberately indistinguishable from a
    non-existent session, so ownership probing yields no oracle). Tasks use the
    identical comparison, and answer an id belonging to someone else with the
    same error as an id that never existed, for the same reason.

    The id is the resolved user's primary key. Unauthenticated principals (an
    ``AnonymousUser`` from a permissive backend such as ``AllowAnyBackend``)
    all map to the shared ``"anonymous"`` principal — ownership is only ever as
    strong as the auth backend behind it.

    ⚠ **Lives in ``auth/``, not ``transport/``, and the move was forced.** It
    is a pure function of a token with nothing transport-specific in it, but it
    used to sit in ``transport/utils.py`` — and ``transport/__init__`` eagerly
    imports both viewsets, which import the handler dispatch table. So a
    *handler* importing this function pulled the whole transport in mid-import
    and closed a cycle. Nothing in ``auth/`` imports anything that could.
    """
    user: Any = token.user
    pk: Any = getattr(user, "pk", None)
    if pk is not None:
        return f"user:{pk}"
    return "anonymous"


__all__ = ["principal_for_token"]
