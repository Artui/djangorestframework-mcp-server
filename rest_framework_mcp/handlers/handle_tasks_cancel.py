from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
    owned_by_caller,
    resolve_task_id,
    unknown_task_error,
)
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.tasks.transition_task import transition_task
from rest_framework_mcp.tasks.types.task_record import TaskRecord


def handle_tasks_cancel(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Signal that a task should stop. Acknowledged with an empty result.

    **Intent, not a guarantee** — the spec's wording is "signals cancellation
    intent". Nothing here reaches into a running worker; it marks the task
    ``cancelled`` so the client stops waiting and a cooperative worker can
    notice. A worker that checks nothing runs to completion, and
    ``transition_task`` then refuses to overwrite ``cancelled``, so the
    record does not flip back.

    Cancelling an already-terminal task is **not** an error: the transition is
    refused, the acknowledgement still goes out, and the next ``tasks/get``
    shows the status that holds. An error would report a fault where the caller
    lost a race it had no way to see.

    ``notifications/cancelled`` is **MUST NOT** for this — it belongs to
    in-flight requests, and a task is work that outlived its request.
    """
    if not declares_tasks_extension(context):
        return missing_capability_error()
    task_id: str | JsonRpcError = resolve_task_id(params)
    if isinstance(task_id, JsonRpcError):
        return task_id

    store = context.tasks
    if store is None:
        return unknown_task_error(task_id)
    record: TaskRecord | None = store.get(task_id)
    if record is None or not owned_by_caller(record, context):
        return unknown_task_error(task_id)

    transition_task(
        store,
        task_id,
        status=TaskStatus.CANCELLED,
        status_message="Cancelled at the client's request.",
        broker=context.subscriptions,
    )
    return {}


__all__ = ["handle_tasks_cancel"]
