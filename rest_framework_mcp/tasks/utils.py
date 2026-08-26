"""Internal helpers shared by the task lifecycle functions."""

from __future__ import annotations

import re
import secrets

from django.utils import timezone

# The spec requires ids a third party cannot enumerate or guess, and that is
# load-bearing here in a way it is not for a session: with no ``tasks/list`` and
# no session to scope a lookup by, unguessability *is* the containment boundary
# — ownership is checked on top of it, not instead of it.
_TASK_ID_BYTES: int = 32


# The alphabet ``token_urlsafe`` draws from, plus a length ceiling: this is the
# shape of an id this package hands out, and the only shape a store here has to
# be able to hold. The ceiling is generous enough for any id shape a custom
# store might mint (a UUID, a ULID) and far below the 250-byte key limit
# memcached enforces.
_TASK_ID_SHAPE = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


def new_task_id() -> str:
    return secrets.token_urlsafe(_TASK_ID_BYTES)


def is_wellformed_task_id(task_id: str) -> bool:
    """Whether ``task_id`` could be an id this package issued.

    A ``taskId`` arrives off the wire and, in a cache-backed store, is
    concatenated straight into a cache key. Django's memcached backends reject
    keys containing spaces or control characters and keys over 250 bytes, so an
    id like ``"a b"`` reaches the client library and raises out of a handler
    that has no arm for it — an unhandled 500 where the client should have got
    the ordinary "unknown task" answer. Nothing is leaked by checking: an id
    that cannot be one we minted cannot name a task that exists, so the two
    answers were always going to be the same.
    """
    return bool(_TASK_ID_SHAPE.match(task_id))


def now_iso() -> str:
    """Current time as the ISO 8601 string the wire format wants.

    ``timezone.now()`` rather than ``datetime.now()`` so the value honours the
    project's ``USE_TZ``: a client in an unknown zone cannot tell a naive local
    timestamp from a UTC one on the wire.
    """
    return timezone.now().isoformat()


__all__ = ["is_wellformed_task_id", "new_task_id", "now_iso"]
