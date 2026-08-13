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
    a user deactivated or stripped of a permission in the meantime is honoured.

    A user that no longer exists degrades to ``AnonymousUser`` rather than
    raising: a deleted account should fail the task's permission checks, and
    fail as a denial the client can read rather than as a worker crash.

    ``raw`` is ``None`` by construction — see
    [`TaskRecord`][rest_framework_mcp.tasks.types.task_record.TaskRecord] for why the
    backend's opaque payload is not persisted."""
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
        return model._default_manager.get(pk=user_pk)
    except model.DoesNotExist:
        return AnonymousUser()


__all__ = ["build_worker_token"]
