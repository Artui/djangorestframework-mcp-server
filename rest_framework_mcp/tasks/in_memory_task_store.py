from __future__ import annotations

from rest_framework_mcp.tasks.types.task_record import TaskRecord


class InMemoryTaskStore:
    """Task store held in one process's memory. **Development and tests only.**

    **Not a deployable backend, and not for the usual reason.** An in-memory *session*
    store is merely restart-fragile; an in-memory *task* store is broken by design,
    because a task is created on a web worker and finished somewhere else — the worker
    writes its result into a dict the web process cannot see, and every poll answers
    "unknown task" until the client gives up. It fails silently and looks like a hung
    job, which is why
    [`DjangoCacheTaskStore`][rest_framework_mcp.tasks.django_cache_task_store.DjangoCacheTaskStore]
    is the default. This class exists so tests and a single-process ``runserver`` can
    exercise the machinery without a cache.

    No expiry: ``ttlMs`` is advisory here, and a process short-lived enough to
    use this store is its own garbage collection.
    """

    def __init__(self) -> None:
        # Per instance, never module-level: two servers in one process keep
        # separate task spaces.
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
