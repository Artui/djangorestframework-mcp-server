"""Internal helpers shared by the task lifecycle functions."""

from __future__ import annotations

import secrets

from django.utils import timezone

# The spec requires ids a third party cannot enumerate or guess, and that is
# load-bearing here in a way it is not for a session: with no ``tasks/list`` and
# no session to scope a lookup by, unguessability *is* the containment boundary
# — ownership is checked on top of it, not instead of it.
_TASK_ID_BYTES: int = 32


def new_task_id() -> str:
    return secrets.token_urlsafe(_TASK_ID_BYTES)


def now_iso() -> str:
    """Current time as the ISO 8601 string the wire format wants.

    ``timezone.now()`` rather than ``datetime.now()`` so the value honours the
    project's ``USE_TZ``: a client in an unknown zone cannot tell a naive local
    timestamp from a UTC one on the wire.
    """
    return timezone.now().isoformat()


__all__ = ["new_task_id", "now_iso"]
