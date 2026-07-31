from __future__ import annotations

from rest_framework_mcp.constants import JSONRPC_VERSION, TASK_STATUS_METHOD
from rest_framework_mcp.subscriptions.publish_invalidations import publish_after_commit
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.subscriptions.utils import topic_for_task
from rest_framework_mcp.tasks.types.task_record import TaskRecord


def publish_task_status(broker: SubscriptionBroker | None, record: TaskRecord) -> None:
    """Announce a task's new status to whoever subscribed to that task.

    ⚠ **The payload is the whole task, not a delta.** The spec: each
    notification carries "a complete ``DetailedTask`` for the current status,
    identical to what ``tasks/get`` would have returned at that moment". So a
    client that misses one loses nothing — the next carries the full state —
    which is what lets notifications stay best-effort while polling remains
    optional rather than a fallback the client has to implement anyway.

    Routed through :func:`publish_after_commit` for the same reason
    invalidations are: a task moving to ``completed`` inside a transaction that
    then rolls back would otherwise announce work that did not happen.

    ⛔ ``notifications/progress`` and ``notifications/message`` are **MUST NOT**
    on this stream, per the extension. Progress for a task is the task's own
    status; the progress channel belongs to the request-scoped stream, which a
    task by definition outlived.
    """
    # The ``broker is None`` case is owned by ``publish_after_commit``, so a
    # server that pushes nothing takes exactly one path rather than two that
    # could drift.
    publish_after_commit(
        broker,
        topic_for_task(record.task_id),
        {
            "jsonrpc": JSONRPC_VERSION,
            "method": TASK_STATUS_METHOD,
            "params": record.to_wire().to_dict(),
        },
    )


__all__: list[str] = ["publish_task_status"]
