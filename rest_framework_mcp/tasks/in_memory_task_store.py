from __future__ import annotations

from rest_framework_mcp.tasks.types.task_record import TaskRecord


class InMemoryTaskStore:
    """Task store held in one process's memory. **Development and tests only.**

    ⚠ **Not a deployable backend, and the reason is not the usual one.** An
    in-memory *session* store is merely restart-fragile; an in-memory *task*
    store is broken by design, because a task is created on a web worker and
    finished somewhere else. Under any real deployment the worker writes its
    result into a dict the web process cannot see, and every poll answers
    "unknown task" until the client gives up. It fails silently and looks like
    a hung job.

    :class:`DjangoCacheTaskStore` is the default for exactly this reason. This
    class exists so tests and a single-process ``runserver`` can exercise the
    machinery without a cache backend.

    No expiry: ``ttlMs`` is advisory here, and a process short-lived enough to
    use this store is its own garbage collection.
    """

    def __init__(self) -> None:
        # Per instance, never module-level — two servers in one process keep
        # separate task spaces, and a test cannot leak into the next one.
        self._records: dict[str, TaskRecord] = {}

    def create(self, record: TaskRecord) -> None:
        self._records[record.task_id] = record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def save(self, record: TaskRecord) -> None:
        self._records[record.task_id] = record

    def delete(self, task_id: str) -> None:
        self._records.pop(task_id, None)


__all__ = ["InMemoryTaskStore"]
