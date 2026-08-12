from __future__ import annotations

from dataclasses import replace
from typing import Any

from rest_framework_mcp.constants import JsonRpcErrorCode, TaskStatus
from rest_framework_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
    owned_by_caller,
    resolve_task_id,
    unknown_task_error,
)
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.subscriptions.publish_after_task import publish_task_status
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from rest_framework_mcp.tasks.utils import now_iso


def handle_tasks_update(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Deliver the client's answers to a task's outstanding input requests.

    The half of the extension that makes a task interactive: a worker needing
    something only the client can supply parks the task in ``input_required``
    with keyed ``inputRequests``; the client answers them here.

    Three rules from the spec, all about being forgiving in the right places:

    - **Unknown keys are ignored, not rejected.** A client that polls twice can
      answer the same request twice, and an invented key is not worth failing
      the whole call over.
    - **A partial set is accepted**, so answers accumulate rather than replace.
    - **Only when nothing is left outstanding** does the task go back to
      ``working``; until then the next ``tasks/get`` shows what is still wanted.

    **Updating a task that is not waiting for input is refused.** The keys are
    the correlation, and a task in ``working`` has none outstanding, so every
    answer would be an unknown key: silently ignored, acknowledged as success,
    and indistinguishable from a delivery that worked.

    **Answers arrive from the client and are model- or user-authored.** The spec
    requires the same trust model as an elicitation response, which is none:
    nothing here interprets a value, and a worker reading them owes them the
    validation it would give any request body.
    """
    if not declares_tasks_extension(context):
        return missing_capability_error()
    task_id: str | JsonRpcError = resolve_task_id(params)
    if isinstance(task_id, JsonRpcError):
        return task_id
    assert isinstance(params, dict)  # noqa: S101 — resolve_task_id proved it

    responses: Any = params.get("inputResponses")
    if not isinstance(responses, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "'inputResponses' is required and must be an object keyed by request id",
        )

    store = context.tasks
    if store is None:
        return unknown_task_error(task_id)
    record: TaskRecord | None = store.get(task_id)
    if record is None or not owned_by_caller(record, context):
        return unknown_task_error(task_id)

    if record.status is not TaskStatus.INPUT_REQUIRED:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            f"Task {task_id!r} is not awaiting input (status: {record.status.value}).",
        )

    outstanding: dict[str, Any] = record.task.input_requests or {}
    accepted: dict[str, Any] = {k: v for k, v in responses.items() if k in outstanding}
    merged: dict[str, Any] = {**record.input_responses, **accepted}
    remaining: dict[str, Any] = {k: v for k, v in outstanding.items() if k not in merged}

    updated: TaskRecord = replace(record, input_responses=merged).with_task(
        status=TaskStatus.WORKING if not remaining else TaskStatus.INPUT_REQUIRED,
        input_requests=remaining or None,
        last_updated_at=now_iso(),
        status_message=None if not remaining else record.task.status_message,
    )
    store.save(updated)
    # Answering changes the task's status, so a subscriber watching it hears,
    # exactly as it would for any other transition.
    publish_task_status(context.subscriptions, updated)
    return {}


__all__ = ["handle_tasks_update"]
