"""The permission check before a task is created judges the route the call names.

A task-augmented ``tools/call`` is judged before anything durable exists, so a
denied caller never queues work. That check judged a spec's permission classes
against a stand-in view whose ``kwargs`` were always ``{}``, so a permission
scoping by a route capture refused a task to a caller the same call run inline
was admitted to.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer, TaskPolicy, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.task_dispatch import maybe_create_task
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.tasks.conftest import RecordingExecutor, context, slow_service
from tests.utils import RefusingRateLimit, granting_route

_KINDS = ["service", "selector"]


def _project() -> dict[str, Any]:
    return {"name": "Apollo"}


def _server(
    kind: str,
    permission: type[BasePermission],
    *url_kwargs: UrlKwarg,
    rate_limits: tuple[Any, ...] = (),
) -> tuple[MCPServer, RecordingExecutor]:
    """One task-only tool named ``tool``, of ``kind``, behind ``permission``."""
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), task_store=store, task_executor=executor
    )
    if kind == "service":
        server.register_service_tool(
            name="tool",
            description="Archive a project.",
            spec=ServiceSpec(service=slow_service, atomic=False, permission_classes=[permission]),
            url_kwargs=url_kwargs,
            task_policy=TaskPolicy.REQUIRED,
            rate_limits=list(rate_limits),
        )
    else:
        server.register_selector_tool(
            name="tool",
            description="Read a project.",
            spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_project, permission_classes=[permission]
            ),
            url_kwargs=url_kwargs,
            task_policy=TaskPolicy.REQUIRED,
            rate_limits=list(rate_limits),
        )
    return server, executor


def _create(server: MCPServer, arguments: dict[str, Any]) -> Any:
    return maybe_create_task(server.tools.get("tool"), arguments, context(server))


@pytest.mark.parametrize("kind", _KINDS)
def test_a_task_is_created_for_a_route_the_permission_grants(kind: str) -> None:
    seen: list[dict[str, Any]] = []
    server, executor = _server(
        kind,
        granting_route("project_pk", 7, seen),
        UrlKwarg("project_pk", type="integer", required=True),
    )

    out = _create(server, {"project_pk": 7})

    assert not isinstance(out, JsonRpcError), f"refused: {out!r}"
    assert out["resultType"] == "task"
    assert len(executor.enqueued) == 1
    assert seen == [{"project_pk": 7}]


@pytest.mark.parametrize("kind", _KINDS)
def test_no_task_is_created_for_a_route_the_permission_denies(kind: str) -> None:
    seen: list[dict[str, Any]] = []
    server, executor = _server(
        kind,
        granting_route("project_pk", 7, seen),
        UrlKwarg("project_pk", type="integer", required=True),
    )

    out = _create(server, {"project_pk": 8})

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    assert executor.enqueued == []
    assert seen == [{"project_pk": 8}]


@pytest.mark.parametrize("kind", _KINDS)
def test_a_denied_caller_missing_a_url_kwarg_is_refused_before_any_task_exists(kind: str) -> None:
    # ``project_pk`` is required and missing. The split ahead of the check
    # refuses nothing, so the permission judges the ``tenant`` the call named
    # and its denial is the answer, with no task queued; the rate limit after
    # it is neither reached nor charged.
    seen: list[dict[str, Any]] = []
    limiter = RefusingRateLimit()
    server, executor = _server(
        kind,
        granting_route("tenant", "acme", seen),
        UrlKwarg("project_pk", type="integer", required=True),
        UrlKwarg("tenant"),
        rate_limits=(limiter,),
    )

    denied = _create(server, {"tenant": "beta"})
    admitted = _create(server, {"tenant": "acme"})

    assert isinstance(denied, JsonRpcError)
    assert denied.code == JsonRpcErrorCode.FORBIDDEN
    # The caller the permission admits is the one the rate limit then refuses.
    assert isinstance(admitted, JsonRpcError)
    assert admitted.code == JsonRpcErrorCode.RATE_LIMITED
    assert limiter.consumed == 1
    assert executor.enqueued == []
    assert seen == [{"tenant": "beta"}, {"tenant": "acme"}]


def test_a_chain_tool_has_no_route_and_is_still_judged() -> None:
    # A chain declares no URL kwargs, so its permissions are judged on an
    # empty route, and the check must not trip over the absent declaration.
    seen: list[dict[str, Any]] = []
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), task_store=store, task_executor=executor
    )
    server.register_chain_tool(
        name="tool",
        steps=[ChainStep("archive", ServiceSpec(service=slow_service, atomic=False))],
        task_policy=TaskPolicy.REQUIRED,
        permissions=[_Recording(seen)],
    )

    out = _create(server, {"project_pk": 7})

    assert out["resultType"] == "task"
    assert seen == [True]


class _Recording:
    """An ``MCPPermission`` granting everything and recording that it was asked."""

    def __init__(self, seen: list[Any]) -> None:
        self._seen = seen

    def has_permission(self, request: Any, token: Any) -> bool:
        self._seen.append(True)
        return True


@pytest.mark.django_db(transaction=True)
async def test_the_tools_call_handler_hands_the_route_to_the_task_check() -> None:
    seen: list[dict[str, Any]] = []
    server, executor = _server(
        "service",
        granting_route("project_pk", 7, seen),
        UrlKwarg("project_pk", type="integer", required=True),
    )

    out = await handle_tools_call_async(
        {"name": "tool", "arguments": {"project_pk": 7}}, context(server)
    )

    assert not isinstance(out, JsonRpcError), f"refused: {out!r}"
    assert out["resultType"] == "task"
    assert len(executor.enqueued) == 1
