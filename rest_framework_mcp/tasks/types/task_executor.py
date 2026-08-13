from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskExecutor(Protocol):
    """Where a created task goes to be worked on.

    One method taking one string, because that is the entire seam. The record is
    already in the store when this is called, so the id finds everything, and
    keeping the payload out of the queue message stops the queue holding a copy
    of the arguments that can drift from the stored ones or outlive the task.

    A Celery consumer writes:

        @shared_task
        def run_mcp_task(task_id: str) -> None:
            my_server.run_task(task_id)

        class CeleryExecutor:
            def enqueue(self, task_id: str) -> None:
                run_mcp_task.delay(task_id)

    …and nothing in this package imports Celery. The protocol is satisfied just
    as well by an RQ or Dramatiq call, a ``ThreadPoolExecutor.submit`` in a
    test, or a management command that drains the store on a schedule.

    **``enqueue`` must not run the work.** It is called on the request path with
    the client waiting for its ``CreateTaskResult``, so anything slow here
    reintroduces exactly the blocking the extension exists to remove.

    **Failures here are not silent.** If ``enqueue`` raises, the task is already
    durable and would otherwise sit in ``working`` forever, so the caller marks
    it ``failed`` and the client finds out by polling — a status it can act on,
    rather than a handle that never resolves.
    """

    def enqueue(self, task_id: str) -> None: ...


__all__ = ["TaskExecutor"]
