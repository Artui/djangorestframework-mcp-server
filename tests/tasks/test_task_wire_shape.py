"""The task wire type, the policy branches, and the two task config scalars.

The remaining corners: the status-specific field ``Task.to_dict`` emits, the
``OPTIONAL`` fallbacks that quietly run a tool inline, the ``REQUIRED`` denial,
the async path's task branch, and settings whose ``None`` is meaningful.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.test import override_settings
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, TaskPolicy
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.task_dispatch import maybe_create_task
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.task import Task
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.tasks.conftest import RecordingExecutor, context, slow_service


def _task(**kwargs: Any) -> Task:
    defaults: dict[str, Any] = {
        "task_id": "t",
        "status": TaskStatus.WORKING,
        "created_at": "2026-07-31T10:00:00Z",
        "last_updated_at": "2026-07-31T10:00:00Z",
        "ttl_ms": None,
    }
    defaults.update(kwargs)
    return Task(**defaults)


# ----- Task.to_dict -----


def test_ttl_is_always_present_even_when_null() -> None:
    """``ttlMs: number | null`` in the schema — an omission and a ``null`` mean
    different things to a client deciding when to stop polling."""
    assert _task().to_dict()["ttlMs"] is None


def test_optional_fields_are_omitted_rather_than_nulled() -> None:
    emitted = _task().to_dict()
    assert "pollIntervalMs" not in emitted
    assert "statusMessage" not in emitted


def test_a_status_message_rides_along_when_set() -> None:
    assert _task(status_message="halfway").to_dict()["statusMessage"] == "halfway"


def test_the_status_decides_which_extra_field_appears_not_what_is_stored() -> None:
    """A record holding both a result and an error must not emit a shape no
    variant in the spec describes. The status is the single source of truth."""
    emitted = _task(
        status=TaskStatus.COMPLETED, result={"a": 1}, error={"b": 2}, input_requests={"c": 3}
    ).to_dict()
    assert emitted["result"] == {"a": 1}
    assert "error" not in emitted
    assert "inputRequests" not in emitted


@pytest.mark.parametrize(
    ("status", "key"),
    [
        (TaskStatus.COMPLETED, "result"),
        (TaskStatus.FAILED, "error"),
        (TaskStatus.INPUT_REQUIRED, "inputRequests"),
    ],
)
def test_a_mandatory_field_is_an_empty_object_rather_than_missing(
    status: TaskStatus, key: str
) -> None:
    """The spec makes each mandatory for its status. A client that unwraps it
    unconditionally breaks on a missing key and survives an empty object."""
    assert _task(status=status).to_dict()[key] == {}


def test_a_cancelled_task_carries_no_extra_field() -> None:
    emitted = _task(status=TaskStatus.CANCELLED).to_dict()
    assert set(emitted) == {"taskId", "status", "createdAt", "lastUpdatedAt", "ttlMs"}


def test_terminal_statuses_are_exactly_the_three() -> None:
    assert [s for s in TaskStatus if s.is_terminal] == [
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ]


# ----- the policy branches -----


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(name="policy", auth_backend=AllowAnyBackend(), **kwargs)
    for name, policy in (
        ("p.optional", TaskPolicy.OPTIONAL),
        ("p.required", TaskPolicy.REQUIRED),
    ):
        server.register_service_tool(
            name=name,
            description="x",
            spec=ServiceSpec(service=slow_service, atomic=False),
            task_policy=policy,
        )
    return server


def test_an_optional_tool_runs_inline_when_the_server_cannot_run_tasks() -> None:
    """Falling back is the whole point of ``OPTIONAL`` — a server without a
    queue still serves the tool."""
    server = _server()
    assert maybe_create_task(server._tools.get("p.optional"), {}, context(server)) is None


def test_an_optional_tool_runs_inline_when_the_client_did_not_declare() -> None:
    server = _server(task_store=InMemoryTaskStore(), task_executor=RecordingExecutor())
    assert (
        maybe_create_task(server._tools.get("p.optional"), {}, context(server, declares=False))
        is None
    )


def test_a_permission_denial_stops_the_task_before_it_is_created() -> None:
    """A task is durable, queued work carrying the caller's authorization.
    Creating one for a caller who may not run the tool would put a denied call
    in the queue and let its own check fail in a worker, where the 403 has
    nowhere to go."""
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = MCPServer(
        name="gated",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=executor,
    )

    class _Denies:
        def has_permission(self, request: Any, token: Any) -> bool:
            return False

        def required_scopes(self) -> list[str]:
            return ["things:write"]

    server.register_service_tool(
        name="p.gated",
        description="x",
        spec=ServiceSpec(service=slow_service, atomic=False),
        task_policy=TaskPolicy.REQUIRED,
        permissions=[_Denies()],
    )
    result = maybe_create_task(server._tools.get("p.gated"), {}, context(server))
    assert isinstance(result, JsonRpcError)
    assert result.data == {"requiredScopes": ["things:write"]}
    assert executor.enqueued == []


def test_a_binding_predating_tasks_entirely_is_untouched() -> None:
    """``getattr`` default, so a hand-built binding without the field behaves
    as ``FORBIDDEN`` rather than raising."""
    server = _server()
    binding = replace(server._tools.get("p.optional"), task_policy=TaskPolicy.FORBIDDEN)
    assert maybe_create_task(binding, {}, context(server)) is None


# ----- the async path -----


@pytest.mark.django_db(transaction=True)
async def test_the_async_tool_path_hands_back_a_task_too() -> None:
    """The two ``tools/call`` handlers are parallel implementations, so the
    branch has to exist — and be exercised — in both."""
    store = InMemoryTaskStore()
    server = MCPServer(
        name="async",
        auth_backend=AllowAnyBackend(),
        task_store=store,
        task_executor=RecordingExecutor(store),
    )
    server.register_service_tool(
        name="a.optional",
        description="x",
        spec=ServiceSpec(service=slow_service, atomic=False),
        task_policy=TaskPolicy.OPTIONAL,
    )
    result = await handle_tools_call_async({"name": "a.optional", "arguments": {}}, context(server))
    assert result["resultType"] == "task"


# ----- config -----


def test_the_task_scalars_come_from_settings_by_default() -> None:
    config = build_mcp_config()
    assert config.task_ttl_ms == 86_400_000
    assert config.task_poll_interval_ms == 5_000


def test_an_explicit_value_wins_over_the_setting() -> None:
    assert build_mcp_config(task_ttl_ms=1_000).task_ttl_ms == 1_000


@override_settings(REST_FRAMEWORK_MCP={"TASK_TTL_MS": None, "TASK_POLL_INTERVAL_MS": None})
def test_none_in_settings_means_none_rather_than_unset() -> None:
    """Why these two cannot use the ``x if x is not None else setting`` shape
    the other scalars use: ``None`` is a configured answer here — "never
    expire", "send no poll hint" — not an absent one."""
    config = build_mcp_config()
    assert config.task_ttl_ms is None
    assert config.task_poll_interval_ms is None
