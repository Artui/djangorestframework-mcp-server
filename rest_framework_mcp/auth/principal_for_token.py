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
    same error as an id that never existed, for the same reason.

    The id is the resolved user's primary key. Deliberately unauthenticated
    principals — an ``AnonymousUser`` from a permissive backend such as
    ``AllowAnyBackend``, or a token carrying no user at all — map to the shared
    ``"anonymous"`` principal. That sharing is honest: nobody was identified, so
    ownership is only ever as strong as the auth backend behind it.

    ⛔ **An *authenticated* token with no ``pk`` is refused, not shared.** That
    is the whole of this function's security content. A backend that resolves a
    real caller to something without a primary key — a service-account object, a
    custom principal, a JWT claim wrapper — used to fall through to
    ``"anonymous"`` alongside every other such caller, and the session store
    keys on exactly this string. Two distinct authenticated callers landing on
    one principal can each present the other's session id and be served, and
    tasks use the identical comparison, so the same merge hands over another
    caller's task results.

    ⚠ **It fails silently and it looks like it is working**, which is why it
    raises rather than degrades: every request succeeds, every session
    resolves, and the only symptom is that isolation is not there. A backend
    hitting this is one line from correct — give the principal a ``pk``, or
    return ``AnonymousUser`` and mean it.

    ⭐ **Related to C1 by mechanism, not coincidence.** The un-awaited coroutine
    that authenticated every caller also had no ``pk``, so the same
    misconfiguration that let everyone in *also* collapsed them onto one
    session namespace. Two findings, one incident.

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
    # ``is_authenticated`` is ``False`` on ``AnonymousUser`` and ``True`` on a
    # real Django user, so it separates "nobody was identified" from "somebody
    # was, and we cannot name them". A user object that declares neither is the
    # ambiguous case, and ambiguity here means a shared session namespace.
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
