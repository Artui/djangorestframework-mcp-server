"""A mounted server that can actually run tasks, for the end-to-end suite.

Module-level so the store outlives a single request the way it does in a real
deployment — the whole point of a task is that the process handling the poll is
not the one that created it.
"""

from __future__ import annotations

from typing import Any

from django.urls import path
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, TaskPolicy
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.tasks.conftest import RecordingExecutor, slow_service

STORE = InMemoryTaskStore()
EXECUTOR = RecordingExecutor(STORE)


def _build() -> MCPServer:
    server = MCPServer(
        name="tasks-e2e",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        task_store=STORE,
        task_executor=EXECUTOR,
    )
    for name, policy in (
        ("t.optional", TaskPolicy.OPTIONAL),
        ("t.required", TaskPolicy.REQUIRED),
        ("t.inline", TaskPolicy.FORBIDDEN),
    ):
        server.register_service_tool(
            name=name,
            description="x",
            spec=ServiceSpec(service=slow_service, atomic=False),
            task_policy=policy,
        )
    return server


SERVER = _build()

urlpatterns: list[Any] = [path("mcp/", SERVER.urls)]
