from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.protocol.types.task import Task


@dataclass(frozen=True)
class TaskRecord:
    """What a store holds: the wire :class:`Task` plus what a worker needs.

    A *superset* rather than a parallel type. The extra fields never reach the
    client — no task message has a slot for them — but they are why a task can
    outlive the request that created it: the worker that finishes it shares
    nothing with that request except this record.

    **The scopes are stored, and that is the point.** Without them the worker
    rebuilds a token that proves nothing, and every ``ScopeRequired`` binding
    denies the call it had already been authorized for — silently, long after
    the client was told the work had started.

    ``TokenInfo.raw`` is **not** stored: it is backend-defined credential
    material, and persisting it would put that in a cache with a week-long
    fallback TTL. A rehydrated token has ``raw=None``, so a permission reaching
    into it is one that cannot run as a task.

    **The separation is a security boundary.** :meth:`to_wire` is the only route
    from a record to a message, which is what stops a principal id or a scope
    list leaking into a response because a field was added to the wrong
    dataclass.

    Attributes:
        task: The wire task — the only part a client ever sees.
        tool_name: The tool the worker replays, verbatim.
        arguments: The arguments it replays, verbatim.
        principal_id: The owner, in the form
            :func:`~rest_framework_mcp.auth.principal_for_token.principal_for_token`
            already produces for sessions.
        user_pk: Rehydrates the user on the worker.
        scopes: Rebuild the worker's ``TokenInfo`` so its permission checks see
            what the request path saw.
        audience: Rebuilds that ``TokenInfo``'s audience.
        enqueued: Whether the task reached the executor, so a worker cannot be
            tricked into running it twice.
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
    what a task's ``progress`` kwarg-pool seed resolves to. **Server-side only,
    by protocol**: the wire ``Task`` carries ``statusMessage`` and no numeric
    field, so a polling client sees only the *rendered* string this and
    :attr:`total` produce."""

    total: float | None = None
    """What :attr:`progress` counts toward, or ``None`` for an open-ended count.

    ``None`` is the ordinary case for work that cannot say how much there is —
    the reporter renders a bare count rather than inventing a denominator."""

    input_responses: dict[str, Any] = field(default_factory=dict)
    """Answers the client has supplied via ``tasks/update``, keyed as the
    matching ``inputRequests`` were.

    Accumulated rather than replaced, because the spec lets a client answer a
    strict subset of what is outstanding and come back for the rest; a worker
    parked on ``input_required`` reads this to find out what it was told.

    The spec requires the server never to reuse a key over a task's lifetime, so
    a key appearing here is answered for good and a later request picks a new
    one."""

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
        ...))``.
        """
        return replace(self, task=replace(self.task, **changes))


__all__ = ["TaskRecord"]
