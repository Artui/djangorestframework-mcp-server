from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskExecutor(Protocol):
    """Where a created task goes to be worked on.

    One method, taking one string, because that is the entire seam. The record
    is already in the store when this is called, so the id is enough to find
    everything — and keeping the payload out of the queue message means the
    queue never holds a copy of the arguments that can drift from the stored
    ones, or outlive the task.

    A Celery consumer writes::

        @shared_task
        def run_mcp_task(task_id: str) -> None:
            my_server.run_task(task_id)

        class CeleryExecutor:
            def enqueue(self, task_id: str) -> None:
                run_mcp_task.delay(task_id)

    …and nothing in this package imports Celery. ⚠ **That is deliberate and
    load-bearing.** "Celery-or-any": the protocol is satisfied by an RQ or
    Dramatiq call, a ``ThreadPoolExecutor.submit`` in a test, or a management
    command that drains the store on a schedule. This package has no opinion
    about which, and no dependency on any of them.

    ⚠ **``enqueue`` must not run the work.** It is called on the request path,
    with the client waiting for its ``CreateTaskResult``; anything slow here
    reintroduces exactly the blocking the extension exists to remove. It should
    hand off and return.

    ⚠ **Failures here are not silent.** If ``enqueue`` raises, the task is
    already durable and would otherwise sit in ``working`` forever, so the
    caller marks it ``failed`` and lets the client find out by polling — a
    status a client can act on, rather than a handle that never resolves.
    """

    def enqueue(self, task_id: str) -> None: ...


__all__ = ["TaskExecutor"]
