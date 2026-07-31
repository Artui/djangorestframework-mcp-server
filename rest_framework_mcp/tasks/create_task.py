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

    **Order matters and is normative.** The record is durable before
    ``enqueue`` is called, and ``enqueue`` returns before the handle goes back
    to the client. The spec requires the first half — *"a server MUST NOT
    return CreateTaskResult until the task is durably created, that is, until a
    tasks/get for the returned taskId would resolve"* — and the second half
    follows from it: a worker that picks the task up on another machine the
    instant it is queued must find it already written.

    ⚠ **A failed hand-off is reported as a failed task, not a failed request.**
    By the time ``enqueue`` raises, the task exists; returning a JSON-RPC error
    would leave the client with no handle and this server with a record stuck
    in ``working`` until its TTL. So the task is moved to ``failed`` and the
    handle is returned anyway — the client's very first ``tasks/get`` tells it
    what happened, through the channel it was already going to use. A seed
    state other than ``working`` is explicitly allowed ("typically, though not
    necessarily").

    The broker exception is deliberately **not** swallowed into a generic
    message: ``statusMessage`` carries its text, because "the queue is down" is
    the single most useful thing a developer can be told here and there is no
    other channel it would reach them by.
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
    # the queue is distinguishable from one that did — see ``run_task``, which
    # refuses to run a task twice by clearing this flag as it starts.
    store.save(replace(record, enqueued=True))
    return seed


__all__ = ["create_task"]
