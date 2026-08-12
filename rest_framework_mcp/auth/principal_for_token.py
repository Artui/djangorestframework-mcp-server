from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.auth.types.token_info import TokenInfo


def principal_for_token(token: TokenInfo) -> str:
    """Derive the stable principal id that owns a session or a task.

    Sessions minted at ``initialize`` are owned by the authenticated principal;
    every subsequent request must present the same one or the session is
    treated as unknown (404 — deliberately indistinguishable from a
    non-existent session, so ownership probing yields no oracle). Tasks use the
    identical comparison, and answer an id belonging to someone else with the
    same error as an id that never existed.

    The id is the resolved user's primary key. Deliberately unauthenticated
    principals — an ``AnonymousUser`` from a permissive backend such as
    ``AllowAnyBackend``, or a token carrying no user at all — map to the shared
    ``"anonymous"`` principal: nobody was identified, so ownership is only ever
    as strong as the auth backend behind it.

    **An *authenticated* token with no ``pk`` is refused, not shared.** The
    session store keys on exactly this string, so a backend resolving real
    callers to objects without a primary key would collapse them onto one
    principal, each able to present the others' session ids and read their task
    results. That fails silently and looks like it is working — every request
    succeeds and the only symptom is the missing isolation — hence the raise
    rather than a fallback.

    Lives in ``auth/`` rather than ``transport/`` because handlers call it and
    ``transport/__init__`` eagerly imports the viewsets, so a home in
    ``transport`` closes an import cycle.
    """
    user: Any = token.user
    pk: Any = getattr(user, "pk", None)
    if pk is not None:
        return f"user:{pk}"
    # ``is_authenticated`` separates "nobody was identified" from "somebody was,
    # and we cannot name them". A user object declaring neither is ambiguous,
    # and ambiguity here means a shared session namespace.
    if user is None or getattr(user, "is_authenticated", None) is False:
        return "anonymous"
    raise ImproperlyConfigured(
        f"The auth backend resolved an authenticated caller to {type(user).__name__}, "
        "which has no 'pk'. Sessions and tasks are owned by a principal id derived "
        "from that primary key, so every such caller would share one principal — "
        "each able to present the others' session ids and read their task results. "
        "Give the resolved user a 'pk', or return AnonymousUser if the caller is "
        "genuinely unauthenticated."
    )


__all__ = ["principal_for_token"]
