from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.tasks.types.task_record import TaskRecord


def build_worker_token(record: TaskRecord) -> TokenInfo:
    """Rebuild the authorization context a task was created under.

    The worker has no request and no bearer token — only what the record kept.
    This turns that back into the ``TokenInfo`` the permission classes expect,
    so the binding's guards run in the worker exactly as they ran inline.

    **The user is re-read from the database, not reconstructed.** A task may sit
    in a queue long enough for the answers permissions ask of it to change, and
    the point in time that matters for authorization is when the work runs — so
    a user stripped of a permission or of group membership in the meantime is
    honoured.

    **A user that is gone or deactivated degrades to ``AnonymousUser``.**
    Deletion is the obvious case, and it degrades rather than raising because a
    deleted account should fail the task's permission checks as a denial the
    client can read, not as a worker crash. Deactivation has to be spelled out
    the same way: ``is_active`` is not something the permission classes
    downstream consult — ``IsAuthenticated`` is true of an inactive user — so
    re-reading the row would otherwise honour deactivation in appearance only.
    This is the check Django's own ``ModelBackend`` makes before it will
    authenticate anyone, applied at the moment the work runs.

    **What is *not* re-derived: ``scopes`` and ``audience``.** They are replayed
    verbatim from the record, because ``raw`` — the backend handle that could be
    re-validated — is deliberately not persisted (see
    [`TaskRecord`][rest_framework_mcp.tasks.types.task_record.TaskRecord]), so
    there is nothing here to re-check them against. A token revoked or narrowed
    after the task was created therefore still buys the task the scopes it was
    created with, for as long as the record survives. ``TASK_TTL_MS`` is what
    bounds that window, and is worth setting deliberately on a server whose
    tokens are short-lived.

    ``raw`` on the rebuilt token is ``None`` for the same reason."""
    return TokenInfo(
        user=_user(record.user_pk),
        scopes=record.scopes,
        audience=record.audience,
    )


def _user(user_pk: Any) -> Any:
    if user_pk is None:
        return AnonymousUser()
    model = get_user_model()
    try:
        user: Any = model._default_manager.get(pk=user_pk)
    except model.DoesNotExist:
        return AnonymousUser()
    # ``getattr`` default: a custom user model need not carry the flag, and
    # absence means "nothing here disables this account".
    if not getattr(user, "is_active", True):
        return AnonymousUser()
    return user


__all__ = ["build_worker_token"]
