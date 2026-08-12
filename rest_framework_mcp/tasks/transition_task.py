from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.subscriptions.publish_after_task import publish_task_status
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
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
    broker: SubscriptionBroker | None = None,
) -> TaskRecord | None:
    """Move a task to ``status``, refusing to reopen a finished one.

    Returns the record as it now stands, or ``None`` if there is no such task.

    **The terminal guard is the whole reason this exists** rather than each
    caller writing its own ``store.save``. Two writers race by construction —
    the worker finishing the job and a ``tasks/cancel`` from the client — and
    no backend here locks, so without a guard a cancel landing just after
    completion would move a task from ``completed`` back to ``cancelled`` and
    tell the client its finished work never ran. The state machine is one-way:
    once ``completed`` / ``failed`` / ``cancelled``, a task stays there.

    A refused transition is **not an error**: the caller asked for something
    already decided, and the record it gets back says what was decided, which is
    what a cancel-after-completion should report. Callers needing to know
    whether they won compare the returned status to the one they asked for.

    ``lastUpdatedAt`` moves only on a transition that happened, so it stays an
    honest "when did this task last change".

    ``broker`` opts the transition into ``notifications/tasks``, published only
    for a transition that *happened* — a refused one changed nothing, and
    saying otherwise would have a subscriber re-read an unmoved status.
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
    publish_task_status(broker, updated)
    return updated


__all__ = ["transition_task"]
