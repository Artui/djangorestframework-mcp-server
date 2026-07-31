from __future__ import annotations

from typing import Any

from rest_framework_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
    owned_by_caller,
    resolve_task_id,
    unknown_task_error,
)
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.tasks.types.task_record import TaskRecord


def handle_tasks_get(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Report a task's current state. **The only way to retrieve a result.**

    Polling is the whole retrieval story — there is no ``tasks/result`` that
    blocks until terminal, and ``pollIntervalMs`` on the record is how the
    server asks for a sane cadence. A client keeps calling this until the
    status is terminal.

    The response is the task itself, and which extra field it carries follows
    from the status: ``result`` when completed, ``error`` when failed,
    ``inputRequests`` when input is required. :meth:`Task.to_dict` owns that
    correspondence, so this handler does not branch on status at all.

    ⚠ ``resultType`` is ``"complete"``, not ``"task"``. The distinction is
    normative and easy to get backwards: ``"task"`` marks *the result that
    hands out a task handle in place of the answer* — nothing else, ever. A
    ``tasks/get`` response is an ordinary complete result that happens to be
    about a task, and stamping it ``"task"`` would tell the client it had just
    been handed a second task.

    Deliberately **not** cacheable, and the only catalog-shaped method here
    that isn't: a task's whole purpose is to change, and the client is asking
    precisely because it wants to know whether it has.
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
    return record.to_wire().to_dict()


__all__ = ["handle_tasks_get"]
