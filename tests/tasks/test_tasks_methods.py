"""``tasks/get``, ``tasks/cancel`` and ``tasks/update``.

Handler-level, so the subject is the decision each one makes rather than the
HTTP shell around it — the wire is covered end-to-end separately.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode, TaskStatus
from rest_framework_mcp.handlers.handle_tasks_cancel import handle_tasks_cancel
from rest_framework_mcp.handlers.handle_tasks_get import handle_tasks_get
from rest_framework_mcp.handlers.handle_tasks_update import handle_tasks_update
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.task import Task
from rest_framework_mcp.tasks.create_task import create_task
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.tasks.transition_task import transition_task
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from tests.tasks.conftest import RecordingExecutor, context

HANDLERS = [handle_tasks_get, handle_tasks_cancel, handle_tasks_update]


def _task(server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor) -> Task:
    return create_task(
        store=store,
        executor=executor,
        tool_name="tasks.optional",
        arguments={},
        token=TokenInfo(user=None),
        ttl_ms=None,
        poll_interval_ms=None,
    )


# ----- the capability gate, on all three -----


@pytest.mark.parametrize("handler", HANDLERS)
def test_a_client_that_did_not_declare_the_extension_is_refused(
    handler: Any, server: MCPServer
) -> None:
    """Per *this* request. A server must not rely on a declaration that did not
    arrive with the request, and there is nowhere to remember one anyway."""
    result = handler({"taskId": "x"}, context(server, declares=False))
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY


def test_the_refusal_tells_the_client_what_to_declare(server: MCPServer) -> None:
    result = handle_tasks_get({"taskId": "x"}, context(server, declares=False))
    assert isinstance(result, JsonRpcError)
    assert "io.modelcontextprotocol/tasks" in result.data["requiredCapabilities"]["extensions"]


def test_the_refusal_is_not_the_code_the_extension_document_prints(server: MCPServer) -> None:
    """⚠ The extension says ``-32003`` while naming the constant the ratified
    core schema allocates as ``-32021``. It is a stale number from when tasks
    were core, and ``-32003`` now sits in the implementation-defined band the
    core spec promises never to define codes in — and is one of the two codes
    this package burned.
    """
    result = handle_tasks_get({"taskId": "x"}, context(server, declares=False))
    assert isinstance(result, JsonRpcError)
    assert result.code == -32021
    assert result.code != -32003


# ----- shape and identity -----


@pytest.mark.parametrize("handler", HANDLERS)
@pytest.mark.parametrize("params", [None, {}, {"taskId": 7}, {"taskId": ""}])
def test_a_missing_or_malformed_task_id_is_invalid_params(
    handler: Any, params: Any, server: MCPServer
) -> None:
    result = handler(params, context(server))
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.INVALID_PARAMS


@pytest.mark.parametrize("handler", HANDLERS)
def test_an_unknown_task_is_invalid_params(handler: Any, server: MCPServer) -> None:
    result = handler({"taskId": "nope", "inputResponses": {}}, context(server))
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.INVALID_PARAMS


@pytest.mark.parametrize("handler", HANDLERS)
def test_another_principals_task_is_indistinguishable_from_a_missing_one(
    handler: Any, server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """⚠ The uniformity is the security property. With no listing and no
    session to scope by, an error that said "not yours" would confirm which
    ids are real — which is the one thing the id's unguessability protects.
    """

    class _User:
        pk = 42

    task: Task = _task(server, store, executor)
    mine = handler({"taskId": task.task_id, "inputResponses": {}}, context(server))
    theirs = handler({"taskId": task.task_id, "inputResponses": {}}, context(server, user=_User()))
    assert isinstance(theirs, JsonRpcError)
    assert theirs.message == f"Unknown task: {task.task_id!r}"
    assert not (isinstance(mine, JsonRpcError) and mine.message == theirs.message)


@pytest.mark.parametrize("handler", HANDLERS)
def test_a_server_with_no_task_store_answers_unknown_task(handler: Any, server: MCPServer) -> None:
    """ "Unknown method" would be worse: the client holds an id and wants to
    know whether it is still good, not whether this endpoint speaks tasks."""
    ctx = replace(context(server), tasks=None)
    result = handler({"taskId": "x", "inputResponses": {}}, ctx)
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.INVALID_PARAMS


# ----- tasks/get -----


def test_get_returns_the_task(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _task(server, store, executor)
    result = handle_tasks_get({"taskId": task.task_id}, context(server))
    assert result["taskId"] == task.task_id
    assert result["status"] == "working"


def test_get_is_a_complete_result_not_a_task_result(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """⚠ Easy to get backwards. ``resultType: task`` marks the result that
    *hands out* a handle; stamping it here would tell the client it had just
    been given a second task."""
    task: Task = _task(server, store, executor)
    assert "resultType" not in handle_tasks_get({"taskId": task.task_id}, context(server))


def test_a_completed_task_carries_its_result(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _task(server, store, executor)
    transition_task(store, task.task_id, status=TaskStatus.COMPLETED, result={"v": 1})
    assert handle_tasks_get({"taskId": task.task_id}, context(server))["result"] == {"v": 1}


def test_a_failed_task_carries_its_error(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _task(server, store, executor)
    transition_task(store, task.task_id, status=TaskStatus.FAILED, error={"code": -32603})
    assert handle_tasks_get({"taskId": task.task_id}, context(server))["error"] == {"code": -32603}


# ----- tasks/cancel -----


def test_cancel_acknowledges_with_an_empty_result(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _task(server, store, executor)
    assert handle_tasks_cancel({"taskId": task.task_id}, context(server)) == {}
    assert store.get(task.task_id).status is TaskStatus.CANCELLED


def test_cancelling_a_finished_task_is_acknowledged_not_refused(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """The caller lost a race it had no way to see. Its next poll shows the
    status that actually holds."""
    task: Task = _task(server, store, executor)
    transition_task(store, task.task_id, status=TaskStatus.COMPLETED, result={})
    assert handle_tasks_cancel({"taskId": task.task_id}, context(server)) == {}
    assert store.get(task.task_id).status is TaskStatus.COMPLETED


# ----- tasks/update -----


def _awaiting_input(store: InMemoryTaskStore, task_id: str, requests: dict[str, Any]) -> None:
    transition_task(store, task_id, status=TaskStatus.INPUT_REQUIRED, input_requests=requests)


def test_answering_every_outstanding_request_resumes_the_task(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _task(server, store, executor)
    _awaiting_input(store, task.task_id, {"k1": {"kind": "elicitation"}})
    assert (
        handle_tasks_update(
            {"taskId": task.task_id, "inputResponses": {"k1": {"name": "Ada"}}}, context(server)
        )
        == {}
    )
    record: TaskRecord = store.get(task.task_id)
    assert record.status is TaskStatus.WORKING
    assert record.input_responses == {"k1": {"name": "Ada"}}
    assert record.task.input_requests is None


def test_a_partial_answer_leaves_the_rest_outstanding(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """The spec allows a strict subset, so answers accumulate rather than
    replace — and the next poll shows exactly what is still wanted."""
    task: Task = _task(server, store, executor)
    _awaiting_input(store, task.task_id, {"k1": {}, "k2": {}})
    handle_tasks_update({"taskId": task.task_id, "inputResponses": {"k1": 1}}, context(server))
    record: TaskRecord = store.get(task.task_id)
    assert record.status is TaskStatus.INPUT_REQUIRED
    assert set(record.task.input_requests) == {"k2"}
    handle_tasks_update({"taskId": task.task_id, "inputResponses": {"k2": 2}}, context(server))
    assert store.get(task.task_id).status is TaskStatus.WORKING
    assert store.get(task.task_id).input_responses == {"k1": 1, "k2": 2}


def test_an_unknown_key_is_ignored_rather_than_rejected(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """A client that polls twice can answer twice, and an invented key is not
    worth failing the whole call over."""
    task: Task = _task(server, store, executor)
    _awaiting_input(store, task.task_id, {"k1": {}})
    handle_tasks_update(
        {"taskId": task.task_id, "inputResponses": {"k1": 1, "made-up": 2}}, context(server)
    )
    assert store.get(task.task_id).input_responses == {"k1": 1}


def test_updating_a_task_that_is_not_awaiting_input_is_refused(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    """⚠ Not pedantry. A ``working`` task has nothing outstanding, so every
    answer would be an unknown key — silently ignored and acknowledged as
    success, indistinguishable from a delivery that worked."""
    task: Task = _task(server, store, executor)
    result = handle_tasks_update(
        {"taskId": task.task_id, "inputResponses": {"k": 1}}, context(server)
    )
    assert isinstance(result, JsonRpcError)
    assert "not awaiting input" in result.message


@pytest.mark.parametrize("responses", [None, "text", 3])
def test_update_requires_an_object_of_responses(
    responses: Any, server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _task(server, store, executor)
    result = handle_tasks_update(
        {"taskId": task.task_id, "inputResponses": responses}, context(server)
    )
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.INVALID_PARAMS


def test_an_input_required_task_with_no_recorded_requests_still_answers(
    server: MCPServer, store: InMemoryTaskStore, executor: RecordingExecutor
) -> None:
    task: Task = _task(server, store, executor)
    transition_task(store, task.task_id, status=TaskStatus.INPUT_REQUIRED)
    assert (
        handle_tasks_update({"taskId": task.task_id, "inputResponses": {"k": 1}}, context(server))
        == {}
    )
    assert store.get(task.task_id).status is TaskStatus.WORKING
