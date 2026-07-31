"""Internal helpers shared by the task lifecycle functions."""

from __future__ import annotations

import secrets

from django.utils import timezone

# 32 bytes of ``secrets`` entropy, URL-safe. The spec requires ids "generated
# with sufficient entropy that a third party cannot enumerate or guess them",
# and it is load-bearing here in a way it is not for a session: there is no
# ``tasks/list``, no session to scope a lookup by, and therefore no second
# layer to fall back on. Unguessability *is* the containment boundary, and
# ownership is checked on top of it rather than instead of it.
_TASK_ID_BYTES: int = 32


def new_task_id() -> str:
    return secrets.token_urlsafe(_TASK_ID_BYTES)


def now_iso() -> str:
    """Current time as the ISO 8601 string the wire format wants.

    ``timezone.now()`` rather than ``datetime.now()`` so the value honours the
    project's ``USE_TZ`` and lands as UTC where it is configured — a task's
    timestamps are read by a client in an unknown zone, and a naive local
    timestamp would be indistinguishable from a UTC one on the wire.
    """
    return timezone.now().isoformat()


__all__ = ["new_task_id", "now_iso"]
