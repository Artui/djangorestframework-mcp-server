from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.tasks.report_task_progress import report_task_progress
from rest_framework_mcp.tasks.transition_task import transition_task
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from rest_framework_mcp.tasks.types.task_store import TaskStore


def run_task(
    store: TaskStore,
    task_id: str,
    *,
    context_factory: Callable[[TaskRecord], MCPCallContext],
) -> None:
    """Execute a stored task and record its outcome. Called **from the worker**.

    The whole of the off-request half. A queue consumer knows only an id;
    everything else — the call, the principal, the scopes — comes back out of
    the store, and the outcome goes back into it. Nothing is returned: the
    client learns the result by polling.

    **Claim before running.** Queues deliver at least once, and running a
    mutation twice because the broker retried would be a data bug with no trace.
    A task is claimed by clearing its ``enqueued`` flag, and one that is not
    claimable — unknown, already claimed, already terminal — is a no-op. This is
    best-effort, not a lock: no backend here does compare-and-swap, so two
    workers entering in the same instant can both claim. It closes the ordinary
    duplicate-delivery case; a consumer that cannot tolerate the residual race
    wants an idempotent service.

    **Permissions run again here, in full.** The stored scopes and user rebuild
    the same ``TokenInfo`` the request carried, so the binding's permissions,
    rate limits and object-permission hook run exactly as they did inline. That
    is why the record stores the authorization context rather than a bare
    principal id: a re-check with a token that proves nothing is not a re-check.

    A denial or any other JSON-RPC error becomes a ``failed`` task — those are
    protocol faults with no protocol response left to carry them. A tool-level
    failure is *not*: it arrives as an ordinary result carrying ``isError:
    true`` and the task is ``completed``, as the spec requires.
    """
    record: TaskRecord | None = store.get(task_id)
    if record is None or not record.enqueued or record.status.is_terminal:
        return
    store.save(replace(record, enqueued=False))

    # The registries, config and identity live on the server and the token on
    # the record; ``MCPServer`` closes over the first half and this supplies the
    # second, which keeps this module free of any import from ``server/``.
    context: MCPCallContext = context_factory(record)
    # ``progress`` is seeded here rather than by ``context_factory`` because only
    # this function knows the task id that makes it addressable. Without it a
    # service's ``progress(...)`` calls resolve to the no-op.
    context = replace(context, progress=report_task_progress(store, task_id))
    # The broker travels on the context so the worker publishes to the same
    # server's subscribers — there is no request here to carry it.
    broker = context.subscriptions
    try:
        result: Any = handle_tools_call(
            {"name": record.tool_name, "arguments": record.arguments}, context
        )
    except Exception as exc:  # noqa: BLE001 — the worker's last line of defence
        # Without this the exception propagates into the queue consumer, the
        # task stays ``working``, and the client polls a handle that resolves
        # only at its TTL. A visible ``failed`` beats a silent hang.
        transition_task(
            store,
            task_id,
            status=TaskStatus.FAILED,
            status_message=f"The task raised an unhandled exception: {exc}",
            broker=broker,
        )
        raise

    if isinstance(result, JsonRpcError):
        transition_task(
            store,
            task_id,
            status=TaskStatus.FAILED,
            status_message=result.message,
            error={"code": int(result.code), "message": result.message, **_data(result)},
            broker=broker,
        )
        return
    transition_task(store, task_id, status=TaskStatus.COMPLETED, result=result, broker=broker)


def _data(error: JsonRpcError) -> dict[str, Any]:
    return {"data": error.data} if error.data is not None else {}


__all__ = ["run_task"]
