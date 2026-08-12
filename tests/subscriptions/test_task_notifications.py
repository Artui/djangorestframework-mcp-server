"""``notifications/tasks`` — a task telling its watchers how it is going.

Closes the loop the tasks extension describes: a client subscribes to task ids
on ``subscriptions/listen`` and stops polling.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.db import transaction
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, SubscriptionFilter
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.handlers.handle_tasks_cancel import handle_tasks_cancel
from rest_framework_mcp.handlers.handle_tasks_update import handle_tasks_update
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.subscriptions.utils import topic_for_task
from rest_framework_mcp.tasks.create_task import create_task
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.tasks.transition_task import transition_task
from tests.subscriptions.test_subscription_core import _context
from tests.tasks.conftest import RecordingExecutor, slow_service

pytestmark = pytest.mark.django_db(transaction=True)


def _server(broker: Any, store: Any) -> MCPServer:
    server = MCPServer(
        name="tasknotify",
        auth_backend=AllowAnyBackend(),
        subscription_broker=broker,
        task_store=store,
        task_executor=RecordingExecutor(store),
    )
    server.register_service_tool(
        name="t.slow",
        description="x",
        spec=ServiceSpec(service=slow_service, atomic=False),
    )
    return server


def _task(store: Any) -> Any:
    return create_task(
        store=store,
        executor=RecordingExecutor(store),
        tool_name="t.slow",
        arguments={},
        token=TokenInfo(user=None),
        ttl_ms=None,
        poll_interval_ms=None,
    )


def _watch(broker: InMemorySubscriptionBroker, task_id: str) -> Any:
    return async_to_sync(broker.subscribe)(frozenset({topic_for_task(task_id)}))


def test_a_transition_reaches_a_watcher() -> None:
    broker, store = InMemorySubscriptionBroker(), InMemoryTaskStore()
    task = _task(store)
    queue = _watch(broker, task.task_id)
    transition_task(
        store, task.task_id, status=TaskStatus.COMPLETED, result={"ok": 1}, broker=broker
    )
    frame = queue.get_nowait()
    assert frame["method"] == "notifications/tasks"
    assert frame["params"]["status"] == "completed"


def test_the_notification_carries_the_whole_task_not_a_delta() -> None:
    """The spec: "a complete DetailedTask ... identical to what tasks/get
    would have returned at that moment". That is what lets a missed
    notification cost nothing and keeps polling genuinely optional."""
    broker, store = InMemorySubscriptionBroker(), InMemoryTaskStore()
    task = _task(store)
    queue = _watch(broker, task.task_id)
    transition_task(
        store, task.task_id, status=TaskStatus.COMPLETED, result={"v": 2}, broker=broker
    )
    params = queue.get_nowait()["params"]
    assert params["taskId"] == task.task_id
    assert params["result"] == {"v": 2}
    assert {"createdAt", "lastUpdatedAt", "ttlMs"} <= set(params)


def test_a_refused_transition_announces_nothing() -> None:
    """It changed nothing, and telling a subscriber otherwise would have it
    re-read a status that had not moved."""
    broker, store = InMemorySubscriptionBroker(), InMemoryTaskStore()
    task = _task(store)
    transition_task(store, task.task_id, status=TaskStatus.COMPLETED, result={}, broker=broker)
    queue = _watch(broker, task.task_id)
    transition_task(store, task.task_id, status=TaskStatus.CANCELLED, broker=broker)
    assert queue.qsize() == 0


def test_an_unknown_task_announces_nothing() -> None:
    broker, store = InMemorySubscriptionBroker(), InMemoryTaskStore()
    queue = _watch(broker, "nope")
    assert transition_task(store, "nope", status=TaskStatus.COMPLETED, broker=broker) is None
    assert queue.qsize() == 0


def test_a_server_with_no_broker_transitions_silently() -> None:
    store = InMemoryTaskStore()
    task = _task(store)
    assert transition_task(store, task.task_id, status=TaskStatus.COMPLETED).status is (
        TaskStatus.COMPLETED
    )


def test_nothing_is_announced_until_the_transaction_commits() -> None:
    """A task moving to ``completed`` inside a transaction that rolls back
    would otherwise announce work that did not happen."""
    broker, store = InMemorySubscriptionBroker(), InMemoryTaskStore()
    task = _task(store)
    queue = _watch(broker, task.task_id)
    with transaction.atomic():
        transition_task(store, task.task_id, status=TaskStatus.COMPLETED, broker=broker)
        assert queue.qsize() == 0, "announced before commit"
    assert queue.qsize() == 1


def test_cancelling_announces() -> None:
    broker, store = InMemorySubscriptionBroker(), InMemoryTaskStore()
    server = _server(broker, store)
    task = _task(store)
    queue = _watch(broker, task.task_id)
    handle_tasks_cancel({"taskId": task.task_id}, _context(server))
    assert queue.get_nowait()["params"]["status"] == "cancelled"


def test_answering_an_input_request_announces() -> None:
    """Fulfilment moves the task back to ``working``, which a watcher should
    hear exactly as it hears any other transition."""
    broker, store = InMemorySubscriptionBroker(), InMemoryTaskStore()
    server = _server(broker, store)
    task = _task(store)
    transition_task(store, task.task_id, status=TaskStatus.INPUT_REQUIRED, input_requests={"k": {}})
    queue = _watch(broker, task.task_id)
    handle_tasks_update({"taskId": task.task_id, "inputResponses": {"k": 1}}, _context(server))
    assert queue.get_nowait()["params"]["status"] == "working"


def test_a_worker_running_a_task_announces_its_outcome() -> None:
    """The end-to-end point of the feature: the client stops polling because
    the worker tells it."""
    broker, store = InMemorySubscriptionBroker(), InMemoryTaskStore()
    server = _server(broker, store)
    task = _task(store)
    queue = _watch(broker, task.task_id)
    server.run_task(task.task_id)
    frame = queue.get_nowait()
    assert frame["params"]["status"] == "completed"
    assert frame["params"]["result"]["structuredContent"] == {"done": "yes"}


# ----- the capability rule at the transport -----


MODERN = "2026-07-28"


def _listen(client: Any, task_ids: list[str], *, declares: bool) -> Any:
    from rest_framework_mcp.constants import TASKS_EXTENSION_ID

    return client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "subscriptions/listen",
                "params": {
                    "notifications": {"taskIds": task_ids},
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": MODERN,
                        "io.modelcontextprotocol/clientCapabilities": {
                            "extensions": {TASKS_EXTENSION_ID: {}} if declares else {}
                        },
                    },
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Method": "subscriptions/listen", "Mcp-Protocol-Version": MODERN},
    )


@pytest.mark.urls("tests.subscriptions.urls")
async def test_asking_for_task_notifications_without_the_capability_is_an_error() -> None:
    """The spec's one exception to "refused entries are dropped": this MUST
    be an error rather than a quiet omission from the acknowledgement."""
    from django.test import AsyncClient

    response = await _listen(AsyncClient(), ["anything"], declares=False)
    assert response.status_code == 400
    body = json.loads(response.content)
    assert body["error"]["code"] == -32021
    assert (
        "io.modelcontextprotocol/tasks"
        in body["error"]["data"]["requiredCapabilities"]["extensions"]
    )


@pytest.mark.urls("tests.subscriptions.urls")
async def test_declaring_the_capability_gets_a_stream_even_with_no_such_task() -> None:
    """The error turns on what the *client declared*, not on anything about the
    tasks it named — so it is not an oracle for which ids exist."""
    from django.test import AsyncClient

    response = await _listen(AsyncClient(), ["nope"], declares=True)
    assert response.status_code == 200
    frame = json.loads((await anext(response.streaming_content))[len(b"data: ") :].strip())
    assert frame["params"]["notifications"] == {}
    await response.streaming_content.aclose()


def test_the_acknowledgement_lists_the_task_ids_it_agreed_to() -> None:
    from rest_framework_mcp.subscriptions.grant_subscription import grant_subscription

    broker, store = InMemorySubscriptionBroker(), InMemoryTaskStore()
    server = _server(broker, store)
    task = _task(store)
    granted, _ = grant_subscription(SubscriptionFilter(task_ids=(task.task_id,)), _context(server))
    assert granted.to_dict()["taskIds"] == [task.task_id]
