"""Shared fixtures for the tasks suite.

The executors here are the point of the seam: neither imports a queue, and the
package never learns which one it got.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, TaskPolicy
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import TASKS_EXTENSION_ID
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


class RecordingExecutor:
    """Records what it was handed instead of queueing it.

    Stands in for the Celery call. The only thing a test needs to assert about
    the hand-off is *that* it happened, with which id, and when relative to the
    store write.
    """

    def __init__(self, store: Any = None) -> None:
        self.enqueued: list[str] = []
        # When set, every ``enqueue`` reads the store back — which is how the
        # "durable before hand-off" requirement is actually observable.
        self._store = store
        self.seen_in_store: list[bool] = []

    def enqueue(self, task_id: str) -> None:
        self.enqueued.append(task_id)
        if self._store is not None:
            self.seen_in_store.append(self._store.get(task_id) is not None)


class BrokenExecutor:
    """An executor whose broker is down."""

    def enqueue(self, task_id: str) -> None:
        raise RuntimeError("broker unreachable")


def slow_service(**_: Any) -> dict[str, str]:
    return {"done": "yes"}


@pytest.fixture
def store() -> InMemoryTaskStore:
    return InMemoryTaskStore()


@pytest.fixture
def executor(store: InMemoryTaskStore) -> RecordingExecutor:
    return RecordingExecutor(store)


@pytest.fixture
def server(store: InMemoryTaskStore, executor: RecordingExecutor) -> MCPServer:
    built = MCPServer(
        name="tasks-fixture",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        task_store=store,
        task_executor=executor,
    )
    for name, policy in (
        ("tasks.optional", TaskPolicy.OPTIONAL),
        ("tasks.required", TaskPolicy.REQUIRED),
        ("tasks.inline", TaskPolicy.FORBIDDEN),
    ):
        built.register_service_tool(
            name=name,
            description="x",
            spec=ServiceSpec(service=slow_service, atomic=False),
            task_policy=policy,
        )
    return built


def context(
    server: MCPServer,
    *,
    declares: bool = True,
    user: Any = None,
    scopes: tuple[str, ...] = (),
) -> MCPCallContext:
    """A request context, with or without the tasks extension declared."""
    from django.http import HttpRequest

    http_request = HttpRequest()
    http_request.method = "POST"
    http_request.user = user
    return MCPCallContext(
        http_request=http_request,
        token=TokenInfo(user=user, scopes=scopes),
        tools=server._tools,
        resources=server._resources,
        prompts=server._prompts,
        protocol_version="2026-07-28",
        client_capabilities=(
            {"extensions": {TASKS_EXTENSION_ID: {}}} if declares else {"extensions": {}}
        ),
        tasks=server.task_store,
        task_executor=server.task_executor,
        config=server._config,
    )
