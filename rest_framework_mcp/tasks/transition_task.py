from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from rest_framework_mcp.tasks.types.task_store import TaskStore
from rest_framework_mcp.tasks.utils import now_iso


def transition_task(
    store: TaskStore,
    task_id: str,
    *,
    status: TaskStatus,
    status_message: str | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    input_requests: dict[str, Any] | None = None,
) -> TaskRecord | None:
    """Move a task to ``status``, refusing to reopen a finished one.

    Returns the record as it now stands, or ``None`` if there is no such task.

    ⚠ **The terminal guard is the whole reason this exists** rather than each
    caller writing its own ``store.save``. Two writers race by construction:
    the worker finishing the job, and a ``tasks/cancel`` arriving from the
    client. Whoever writes second wins in every backend here — none of them
    lock — so without a guard a cancel landing just after completion would move
    a task from ``completed`` back to ``cancelled``, and the client would be
    told its finished work never ran. The state machine is one-way: once
    ``completed`` / ``failed`` / ``cancelled``, a task stays there.

    A refused transition is **not an error**. The caller asked for something
    that has already been decided, and the record it gets back says what was
    decided — which is exactly what a cancel-after-completion should report.
    Callers that need to know whether they won compare the returned status to
    the one they asked for.

    ``lastUpdatedAt`` moves only on a transition that actually happened, so it
    stays an honest "when did this task last change" rather than "when was it
    last written to".
    """
    record: TaskRecord | None = store.get(task_id)
    if record is None:
        return None
    if record.status.is_terminal:
        return record

    updated: TaskRecord = record.with_task(
        status=status,
        last_updated_at=now_iso(),
        status_message=status_message,
        result=result,
        error=error,
        input_requests=input_requests,
    )
    store.save(updated)
    return updated


__all__ = ["transition_task"]
