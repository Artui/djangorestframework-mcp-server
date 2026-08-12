"""Creating tasks, moving them between states, and running them.

The three functions that own a task's life: ``create_task`` on the request
path, ``transition_task`` wherever a status changes, and ``run_task`` in the
worker.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.protocol.types.task import Task
from rest_framework_mcp.tasks.build_worker_token import build_worker_token
from rest_framework_mcp.tasks.create_task import create_task
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.tasks.transition_task import transition_task
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from tests.tasks.conftest import BrokenExecutor, RecordingExecutor, slow_service


def _register(server: MCPServer, name: str, service: Any, **kwargs: Any) -> None:
    from rest_framework_services.types.service_spec import ServiceSpec

    server.register_service_tool(
        name=name, description="x", spec=ServiceSpec(service=service, atomic=False), **kwargs
    )


def _create(store: Any, executor: Any, **kwargs: Any) -> Task:
    defaults: dict[str, Any] = {
        "tool_name": "tasks.optional",
        "arguments": {},
        "token": TokenInfo(user=None),
        "ttl_ms": 60_000,
        "poll_interval_ms": 500,
    }
    defaults.update(kwargs)
    return create_task(store=store, executor=executor, **defaults)


# ----- create_task -----


def test_the_task_is_durable_before_the_executor_is_called(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Normative, and the one ordering that actually matters.

    The spec: a server MUST NOT return a task handle until a ``tasks/get`` for
    that id would resolve. A worker on another machine can pick the task up the
    instant it is queued, so "write after queueing" is a race that hands out an
    id nothing can find.
    """
    _create(store, executor)
    assert executor.seen_in_store == [True]


def test_the_handle_carries_what_the_client_needs_to_poll(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _create(store, executor)
    assert task.status is TaskStatus.WORKING
    assert task.ttl_ms == 60_000
    assert task.poll_interval_ms == 500
    assert task.created_at == task.last_updated_at


def test_ids_are_unguessable(store: InMemoryTaskStore, executor: RecordingExecutor) -> None:
    """With no ``tasks/list`` and no session, the id *is* the containment
    boundary — so this is a security property, not a nicety."""
    ids = {_create(store, executor).task_id for _ in range(20)}
    assert len(ids) == 20
    assert all(len(i) > 30 for i in ids)


def test_the_authorization_context_is_stored_not_just_the_principal(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Without the scopes, the worker rebuilds a token that proves nothing and
    every ``ScopeRequired`` binding denies work it had already authorized."""
    task: Task = _create(
        store, executor, token=TokenInfo(user=None, scopes=("a", "b"), audience="aud")
    )
    record: TaskRecord = store.get(task.task_id)
    assert record.scopes == ("a", "b")
    assert record.audience == "aud"
    assert record.principal_id == "anonymous"


def test_a_broker_that_is_down_produces_a_failed_task_not_a_failed_request(
    store: InMemoryTaskStore,
) -> None:
    """By the time ``enqueue`` raises the task exists, so returning an error
    would leave the client with no handle and a record stuck in ``working``."""
    task: Task = _create(store, BrokenExecutor())
    assert task.status is TaskStatus.FAILED
    assert "broker unreachable" in (task.status_message or "")
    assert store.get(task.task_id).status is TaskStatus.FAILED


def test_a_successful_hand_off_marks_the_task_claimable(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _create(store, executor)
    assert store.get(task.task_id).enqueued is True


def test_a_failed_hand_off_leaves_the_task_unclaimable(store: InMemoryTaskStore) -> None:
    task: Task = _create(store, BrokenExecutor())
    assert store.get(task.task_id).enqueued is False


# ----- transition_task -----


def test_a_finished_task_cannot_be_reopened(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """The race this exists for: a cancel landing just after completion.

    Nothing here locks, so the second writer would otherwise win and the client
    would be told its finished work never ran.
    """
    task: Task = _create(store, executor)
    transition_task(store, task.task_id, status=TaskStatus.COMPLETED, result={"ok": 1})
    after = transition_task(store, task.task_id, status=TaskStatus.CANCELLED)
    assert after.status is TaskStatus.COMPLETED
    assert after.task.result == {"ok": 1}


def test_a_refused_transition_returns_the_state_that_actually_holds(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Not an error — the caller asked for something already decided, and the
    record it gets back is the answer."""
    task: Task = _create(store, executor)
    transition_task(store, task.task_id, status=TaskStatus.CANCELLED)
    assert transition_task(store, task.task_id, status=TaskStatus.COMPLETED).status is (
        TaskStatus.CANCELLED
    )


def test_transitioning_an_unknown_task_is_none(store: InMemoryTaskStore) -> None:
    assert transition_task(store, "nope", status=TaskStatus.COMPLETED) is None


def test_a_real_transition_moves_the_timestamp(
    store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _create(store, executor)
    moved = transition_task(store, task.task_id, status=TaskStatus.COMPLETED, result={})
    assert moved.task.last_updated_at >= task.last_updated_at


# ----- run_task -----


@pytest.mark.django_db
def test_running_a_task_completes_it_with_the_tool_result(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _create(store, executor)
    server.run_task(task.task_id)
    record: TaskRecord = store.get(task.task_id)
    assert record.status is TaskStatus.COMPLETED
    assert record.task.result["structuredContent"] == {"done": "yes"}


@pytest.mark.django_db
def test_a_redelivered_task_runs_once(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Queues deliver at least once. Running a mutation twice because the
    broker retried would be a data bug with no trace."""
    calls: list[int] = []

    def counting(**_: Any) -> dict[str, str]:
        calls.append(1)
        return {"n": str(len(calls))}

    _register(server, "tasks.counting", counting)
    task: Task = _create(store, executor, tool_name="tasks.counting")
    server.run_task(task.task_id)
    server.run_task(task.task_id)
    assert calls == [1]


@pytest.mark.django_db
def test_running_an_unknown_task_is_a_no_op(server: MCPServer) -> None:
    server.run_task("never-existed")


@pytest.mark.django_db
def test_running_a_task_that_never_reached_the_queue_is_a_no_op(
    server: MCPServer, store: InMemoryTaskStore
) -> None:
    task: Task = _create(store, BrokenExecutor())
    server.run_task(task.task_id)
    assert store.get(task.task_id).status is TaskStatus.FAILED


@pytest.mark.django_db
def test_a_protocol_error_becomes_a_failed_task(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """A JSON-RPC error has no response left to ride in — the client is long
    gone — so the only place to put it is the task's own status."""
    task: Task = _create(store, executor, tool_name="does.not.exist")
    server.run_task(task.task_id)
    record: TaskRecord = store.get(task.task_id)
    assert record.status is TaskStatus.FAILED
    assert record.task.error["code"] == -32602


@pytest.mark.django_db
def test_an_unhandled_exception_fails_the_task_and_still_propagates(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """Failing the task stops the client polling forever; re-raising lets the
    queue consumer record the traceback an operator needs."""

    def explode(**_: Any) -> Any:
        raise RuntimeError("boom")

    _register(server, "tasks.explode", explode)
    task: Task = _create(store, executor, tool_name="tasks.explode")
    with pytest.raises(RuntimeError, match="boom"):
        server.run_task(task.task_id)
    assert store.get(task.task_id).status is TaskStatus.FAILED


@pytest.mark.django_db
def test_a_worker_does_not_charge_the_rate_limit_a_second_time(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """The limit was consumed when the client asked. Charging again on replay
    would bill one client call twice and halve every configured quota."""
    consumed: list[int] = []

    class _Limiter:
        def consume(self, request: Any, token: Any) -> int | None:
            consumed.append(1)
            return None

    _register(server, "tasks.limited", slow_service, rate_limits=[_Limiter()])
    task: Task = _create(store, executor, tool_name="tasks.limited")
    server.run_task(task.task_id)
    assert consumed == []


def test_run_task_on_a_server_with_no_store_refuses_loudly() -> None:
    """Silence here would look like success: the job would finish and the
    client would poll a handle forever."""
    from django.core.exceptions import ImproperlyConfigured

    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend

    bare = MCPServer(name="bare", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="no task store"):
        bare.run_task("anything")


# ----- build_worker_token -----


@pytest.mark.django_db
def test_the_worker_rereads_the_user_rather_than_reconstructing_it(
    django_user_model: Any,
) -> None:
    """A task can sit in a queue long enough for the answers permissions ask to
    change. The point in time that matters is when the work runs."""
    user = django_user_model.objects.create_user(username="u1")
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
        principal_id=f"user:{user.pk}",
        user_pk=user.pk,
        scopes=("s",),
    )
    token = build_worker_token(record)
    assert token.user.pk == user.pk
    assert token.scopes == ("s",)
    assert token.raw is None


@pytest.mark.django_db
def test_a_deleted_user_degrades_to_anonymous_rather_than_crashing(
    django_user_model: Any,
) -> None:
    """A deleted account should fail the task's permission checks — which is
    what anonymous does — and fail as a denial, not a worker crash."""
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
        principal_id="user:9999",
        user_pk=9999,
    )
    assert isinstance(build_worker_token(record).user, AnonymousUser)


def test_a_task_created_by_an_anonymous_caller_stays_anonymous() -> None:
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
        principal_id="anonymous",
    )
    assert isinstance(build_worker_token(record).user, AnonymousUser)


def test_with_task_leaves_the_original_untouched() -> None:
    record = TaskRecord(
        task=Task(
            task_id="t",
            status=TaskStatus.WORKING,
            created_at="x",
            last_updated_at="x",
            ttl_ms=None,
        ),
        tool_name="t",
        arguments={},
        principal_id="anonymous",
    )
    changed = record.with_task(status=TaskStatus.COMPLETED)
    assert record.status is TaskStatus.WORKING
    assert changed.status is TaskStatus.COMPLETED
    assert replace(changed, enqueued=True).to_wire() is changed.task
