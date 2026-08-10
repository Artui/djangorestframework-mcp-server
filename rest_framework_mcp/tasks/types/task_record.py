from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.protocol.types.task import Task


@dataclass(frozen=True)
class TaskRecord:
    """What a store holds: the wire :class:`Task` plus what a worker needs.

    Deliberately a *superset* rather than a parallel type. The extra fields
    never reach the client and could not — no task message has a slot for
    them — but they are the whole reason a task can outlive the request that
    created it. The worker that finishes it shares nothing with that request
    except this record.

    Three groups:

    - **The call.** :attr:`tool_name` and :attr:`arguments`, replayed verbatim.
    - **The authorization context.** :attr:`principal_id` (the form
      :func:`~rest_framework_mcp.auth.principal_for_token.principal_for_token` already
      produces for sessions), :attr:`user_pk` to rehydrate the user, and
      :attr:`scopes` / :attr:`audience` to rebuild the ``TokenInfo``.
    - **Bookkeeping.** :attr:`enqueued`, so a worker cannot be tricked into
      running the same task twice, and :attr:`progress` / :attr:`total`, which
      are where a running task's ``progress(...)`` calls land.

    ⚠ **The scopes are stored, and that is the point.** Without them the worker
    would rebuild a token that proves nothing, and every ``ScopeRequired``
    binding would deny the call it had already been authorized for — silently,
    long after the client was told the work had started. Storing them lets the
    worker re-run the *same* permission checks the request path ran, which is
    defence in depth rather than a shortcut around it.

    ⛔ ``TokenInfo.raw`` is **not** stored. It is backend-defined and holds
    whatever the backend felt like keeping — an ``AccessToken`` row, decoded JWT
    claims — so persisting it would put credential material in a cache with a
    week-long fallback TTL. A rehydrated token has ``raw=None``; a permission
    that reaches into it is one that cannot run as a task, which is a fair
    trade for not writing tokens to the cache.

    ⚠ **The separation is a security boundary, so keep it.** :meth:`to_wire` is
    the only route from a record to a message, which is what stops a principal
    id or a scope list leaking into a response because someone added a field to
    the wrong dataclass.
    """

    task: Task
    tool_name: str
    arguments: dict[str, Any]
    principal_id: str
    user_pk: Any = None
    scopes: tuple[str, ...] = field(default_factory=tuple)
    audience: str | None = None
    enqueued: bool = False

    progress: float | None = None
    """How far along the running task said it was, or ``None`` if it never said.

    Written by
    :func:`~rest_framework_mcp.tasks.report_task_progress.report_task_progress`,
    which is what a task's ``progress`` kwarg-pool seed resolves to. Paired with
    :attr:`total` when the service supplied one.

    ⚠ **Server-side only, by protocol.** The wire ``Task`` carries
    ``statusMessage`` and no numeric field, so a polling client only ever sees
    the *rendered* string these two produce. Keeping the numbers is still worth
    it: they are what makes the value legible in logs and the admin, and they
    are the input a future adaptive ``pollIntervalMs`` would need. Rendering is
    lossy and rendering back is not a thing."""

    total: float | None = None
    """What :attr:`progress` counts toward, or ``None`` for an open-ended count.

    ``None`` is the ordinary case for work that cannot say how much there is —
    the reporter renders a bare count rather than inventing a denominator."""

    input_responses: dict[str, Any] = field(default_factory=dict)
    """Answers the client has supplied via ``tasks/update``, keyed as the
    matching ``inputRequests`` were.

    Accumulated rather than replaced, because the spec lets a client answer a
    strict subset of what is outstanding and come back for the rest. A worker
    parked on ``input_required`` reads this to find out what it was told.

    ⚠ Keys are never reused over a task's lifetime — the spec requires it of
    the server — so a key appearing here is answered for good, and a later
    request must pick a new one."""

    @property
    def task_id(self) -> str:
        return self.task.task_id

    @property
    def status(self) -> TaskStatus:
        return self.task.status

    def to_wire(self) -> Task:
        return self.task

    def with_task(self, **changes: Any) -> TaskRecord:
        """Return a copy with fields changed on the embedded :class:`Task`.

        Saves every caller a nested ``replace(record, task=replace(record.task,
        ...))``, which is easy to get wrong as a direct assignment to a frozen
        field.
        """
        return replace(self, task=replace(self.task, **changes))


__all__ = ["TaskRecord"]
