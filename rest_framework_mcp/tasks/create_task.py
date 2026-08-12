from __future__ import annotations

from dataclasses import replace
from typing import Any

from rest_framework_mcp.auth.principal_for_token import principal_for_token
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.protocol.types.task import Task
from rest_framework_mcp.tasks.types.task_executor import TaskExecutor
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from rest_framework_mcp.tasks.types.task_store import TaskStore
from rest_framework_mcp.tasks.utils import new_task_id, now_iso


def create_task(
    *,
    store: TaskStore,
    executor: TaskExecutor,
    tool_name: str,
    arguments: dict[str, Any],
    token: TokenInfo,
    ttl_ms: int | None,
    poll_interval_ms: int | None,
) -> Task:
    """Persist a task, hand it to the executor, and return the client's handle.

    **Order matters and is normative.** The spec forbids returning a
    ``CreateTaskResult`` before a ``tasks/get`` for that id would resolve, so
    the record is durable before ``enqueue`` is called — and ``enqueue`` returns
    before the handle goes back, because a worker picking the task up on another
    machine the instant it is queued must find it already written.

    **A failed hand-off is reported as a failed task, not a failed request.** By
    the time ``enqueue`` raises the task exists, so a JSON-RPC error would leave
    the client with no handle and this server with a record stuck in ``working``
    until its TTL. The task moves to ``failed`` — a seed state other than
    ``working`` is explicitly allowed — and the handle is returned anyway, so
    the first ``tasks/get`` reports what happened. The broker's exception text
    rides in ``statusMessage`` rather than being flattened to a generic message,
    since "the queue is down" reaches a developer by no other channel.
    """
    created: str = now_iso()
    seed = Task(
        task_id=new_task_id(),
        status=TaskStatus.WORKING,
        created_at=created,
        last_updated_at=created,
        ttl_ms=ttl_ms,
        poll_interval_ms=poll_interval_ms,
    )
    record = TaskRecord(
        task=seed,
        tool_name=tool_name,
        arguments=arguments,
        principal_id=principal_for_token(token),
        user_pk=getattr(token.user, "pk", None),
        scopes=tuple(token.scopes),
        audience=token.audience,
    )
    store.create(record)

    try:
        executor.enqueue(record.task_id)
    except Exception as exc:  # noqa: BLE001 — any broker failure, reported as one
        failed: TaskRecord = record.with_task(
            status=TaskStatus.FAILED,
            last_updated_at=now_iso(),
            status_message=f"The task could not be queued for execution: {exc}",
        )
        store.save(failed)
        return failed.to_wire()

    # Marked only after a successful hand-off, so a record that never reached
    # the queue stays distinguishable from one that did — ``run_task`` clears
    # the flag as it starts, which is how it refuses to run a task twice.
    store.save(replace(record, enqueued=True))
    return seed


__all__ = ["create_task"]
