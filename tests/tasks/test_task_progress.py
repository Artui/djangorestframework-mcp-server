"""Progress from inside a task — the record is the channel, polling is the pull.

``notifications/progress`` needs a live connection and a worker has none, so a
service running as a task reports onto its own record and the client reads it
through ``tasks/get``. The service body is identical either way; that is the
requirement these tests hold.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.cache import cache

from rest_framework_mcp import MCPServer
from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.protocol.types.task import Task
from rest_framework_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.tasks.report_task_progress import (
    _render_progress,
    report_task_progress,
)
from rest_framework_mcp.tasks.transition_task import transition_task
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from tests.tasks.conftest import RecordingExecutor, context
from tests.tasks.test_task_lifecycle import _create, _register

# ----- rendering -----


@pytest.mark.parametrize(
    ("progress", "total", "message", "expected"),
    [
        (3, 10, "Indexing", "Indexing (3/10)"),
        (3, 10, None, "3/10"),
        (3, None, "Indexing", "Indexing (3)"),
        (3, None, None, "3"),
        # ``:g`` rather than ``str``: the Protocol types these as floats, and
        # "3/10" is what a human reads where "3.0/10.0" is what a repr prints.
        (3.0, 10.0, None, "3/10"),
        (2.5, None, None, "2.5"),
    ],
)
def test_the_status_message_renders_what_the_client_can_actually_see(
    progress: float, total: float | None, message: str | None, expected: str
) -> None:
    assert _render_progress(progress, total, message) == expected


# ----- the reporter -----


@pytest.mark.django_db
def test_a_report_lands_on_the_record_and_on_the_wire(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Numbers server-side, rendered string client-side — both, from one call."""
    task: Task = _create(store, executor)
    report_task_progress(store, task.task_id)(7, total=20, message="Indexing")

    record: TaskRecord = store.get(task.task_id)
    assert (record.progress, record.total) == (7, 20)
    assert record.task.status_message == "Indexing (7/20)"
    # The wire ``Task`` has no numeric slot, so this string is the whole of
    # what a polling client receives.
    assert "progress" not in record.to_wire().to_dict()


@pytest.mark.django_db
def test_a_report_moves_the_timestamp_but_not_the_status(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _create(store, executor)
    before: str = store.get(task.task_id).task.last_updated_at
    report_task_progress(store, task.task_id)(1)

    record: TaskRecord = store.get(task.task_id)
    assert record.status is TaskStatus.WORKING
    assert record.task.last_updated_at >= before


@pytest.mark.django_db
def test_a_terminal_task_is_never_rewritten(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """A cancel must not be un-done by a worker still unwinding.

    The same one-way rule ``transition_task`` holds, for the same reason: the
    two writers race by construction, and the finished state wins.
    """
    task: Task = _create(store, executor)
    transition_task(store, task.task_id, status=TaskStatus.CANCELLED)
    report_task_progress(store, task.task_id)(99, total=100)

    record: TaskRecord = store.get(task.task_id)
    assert record.status is TaskStatus.CANCELLED
    assert record.progress is None
    assert record.task.status_message is None


@pytest.mark.django_db
def test_reporting_on_an_unknown_task_is_a_no_op(store: InMemoryTaskStore) -> None:
    report_task_progress(store, "never-existed")(1)


def test_a_store_that_is_down_does_not_take_the_operation_with_it() -> None:
    """The sink is network I/O on somebody else's box, called once per tick.

    A service that reports progress must not fail because the cache blinked —
    the report describes the work, it is not the work.
    """

    class _BrokenStore:
        def get(self, task_id: str) -> Any:
            raise RuntimeError("cache unreachable")

    report_task_progress(_BrokenStore(), "t-1")(1)  # must not raise


@pytest.mark.django_db
def test_reporting_often_does_not_keep_a_task_alive_forever(
    server: MCPServer, executor: RecordingExecutor
) -> None:
    """The cache store's absolute-expiry design, now that writes actually happen.

    ``DjangoCacheTaskStore`` stamps expiry once at ``create`` and renews to the
    *remaining* lifetime, with the note that otherwise "a task that reports
    progress often would never expire". That was written for a write pattern
    that did not yet exist — this is it, so it gets an assertion rather than a
    re-reading.
    """
    cache.clear()
    store = DjangoCacheTaskStore(namespace="progress")
    task: Task = _create(store, executor, ttl_ms=60_000)
    deadline: Any = cache.get(f"{store._prefix}{task.task_id}")["expiresAt"]

    report = report_task_progress(store, task.task_id)
    for row in range(1, 25):
        report(row, total=24)

    assert cache.get(f"{store._prefix}{task.task_id}")["expiresAt"] == deadline


# ----- end to end -----


@pytest.mark.django_db
def test_a_service_reporting_progress_is_visible_to_a_polling_client(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """The whole point, in one test.

    The service declares ``progress`` and calls it — the same body that would
    stream ``notifications/progress`` inline. Run as a task, those calls have
    to survive to the record, which means the sync dispatch path forwards the
    reporter (it once did not, on the grounds that there was "no stream to
    report on" — true of the connection, false of the worker).
    """
    seen: list[str] = []

    def indexer(progress: Any, **_: Any) -> dict[str, str]:
        for row in (1, 2, 3):
            progress(row, total=3, message="Indexing")
            seen.append(store.get(task.task_id).task.status_message)
        return {"rows": "3"}

    _register(server, "tasks.indexer", indexer)
    task: Task = _create(store, executor, tool_name="tasks.indexer")
    server.run_task(task.task_id)

    assert seen == ["Indexing (1/3)", "Indexing (2/3)", "Indexing (3/3)"]
    record: TaskRecord = store.get(task.task_id)
    assert record.status is TaskStatus.COMPLETED
    # The last report survives the completing transition's own write, because
    # the two touch different fields.
    assert (record.progress, record.total) == (3, 3)


@pytest.mark.django_db
def test_the_same_service_runs_unchanged_off_the_task_path(server: MCPServer) -> None:
    """The no-op seed, which is what lets one spec serve both paths.

    An ordinary sync ``tools/call`` supplies no reporter, drf-services
    substitutes its own no-op, and the identical service body completes without
    knowing the difference. If it did know — if ``progress`` were ``None`` here
    — every spec would need a task-shaped and a request-shaped variant.
    """
    reported: list[Any] = []

    def indexer(progress: Any, **_: Any) -> dict[str, str]:
        progress(1, total=1, message="Indexing")
        reported.append(progress)
        return {"ok": "yes"}

    _register(server, "tasks.plain", indexer)
    result: Any = handle_tools_call(
        {"name": "tasks.plain", "arguments": {}}, context(server, declares=False)
    )

    assert result["structuredContent"] == {"ok": "yes"}
    # Called, and callable — the seed is a no-op, never ``None``.
    assert len(reported) == 1 and callable(reported[0])
