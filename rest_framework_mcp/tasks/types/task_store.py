from __future__ import annotations

from typing import Protocol, runtime_checkable

from rest_framework_mcp.tasks.types.task_record import TaskRecord


@runtime_checkable
class TaskStore(Protocol):
    """Pluggable persistence for tasks, mirroring ``SessionStore``.

    ⚠ **Unlike ``SessionStore``, an in-process implementation is not
    deployable.** A session only ever has to be recognised by the process that
    minted it; a task is created by a web worker and finished by a *different*
    process entirely — that is the whole point of it. So
    :class:`InMemoryTaskStore` is a development and test convenience, and
    :class:`DjangoCacheTaskStore` is the default.

    Four operations, no more:

    - :meth:`create` writes the seed record and **must not return until the
      task is durable**. The spec is explicit: a server must not answer with a
      ``CreateTaskResult`` until a ``tasks/get`` for that id would resolve.
      Answering first and writing after is the one race that produces a task id
      the client cannot use.
    - :meth:`get` reads one back, or ``None`` if it never existed or has
      expired. Both are the same answer to a caller: ``-32602``.
    - :meth:`save` overwrites in place, preserving the original expiry.
    - :meth:`delete` removes one.

    **Expiry belongs to the store.** ``ttlMs`` is on the record, but only the
    store knows what clock its backend keeps, and only the store can drop an
    entry without anyone asking. Callers never compare a timestamp to now.

    ⚠ **No locking, deliberately.** Last write wins, and the guard against a
    finished task being reopened lives in
    :func:`rest_framework_mcp.tasks.transition_task` rather than in each
    backend — a rule enforced in one place beats four backends implementing
    compare-and-swap differently.
    """

    def create(self, record: TaskRecord) -> None: ...

    def get(self, task_id: str) -> TaskRecord | None: ...

    def save(self, record: TaskRecord) -> None: ...

    def delete(self, task_id: str) -> None: ...


__all__ = ["TaskStore"]
