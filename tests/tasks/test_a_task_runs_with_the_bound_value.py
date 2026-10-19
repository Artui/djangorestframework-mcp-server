"""A task-augmented call is judged, and recorded, with the server's value in place.

The binding's ``server_arguments`` are applied before the task is created, so
the permission check at its creation sees the bound value, and the task stores
the arguments the tool will run with: the bound value over the caller's, and
the withheld name dropped. Applied after, the check saw the caller's value and
the task recorded it. Held for both handlers, since each orders the two steps.
"""

from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import SelectorKind, ServerArguments
from rest_framework_services import SelectorSpec, UrlKwarg

from rest_framework_mcp import MCPServer, TaskPolicy
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.tasks.conftest import RecordingExecutor, context
from tests.utils import granting_route


def _rows(*, project_pk: int, draw_request_pk: int | None = None) -> list[dict[str, Any]]:
    return [{"project": project_pk}]


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_task_is_judged_and_recorded_with_the_bound_value(is_async: bool) -> None:
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), task_store=store, task_executor=executor
    )
    seen: list[dict[str, Any]] = []
    server.register_selector_tool(
        name="rows",
        description="Rows.",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_rows,
            permission_classes=[granting_route("project_pk", 7, seen)],
            reads=[
                UrlKwarg("project_pk", type="integer", required=True),
                UrlKwarg("draw_request_pk", type="integer"),
            ],
        ),
        server_arguments=ServerArguments(values={"project_pk": 7}, withheld={"draw_request_pk"}),
        task_policy=TaskPolicy.REQUIRED,
        paginate=True,
    )
    params = {"name": "rows", "arguments": {"project_pk": 8, "draw_request_pk": 3}}

    if is_async:
        out = await handle_tools_call_async(params, context(server))
    else:
        out = handle_tools_call(params, context(server))

    assert isinstance(out, dict) and out["resultType"] == "task", out
    assert seen == [{"project_pk": 7}]
    assert store.get(executor.enqueued[0]).arguments == {"project_pk": 7}
