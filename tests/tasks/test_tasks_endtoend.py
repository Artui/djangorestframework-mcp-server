"""The tasks extension over the actual HTTP transport.

Everything here goes through the URL conf, because the subject is what a
conformant client sees: which result shape comes back, which headers have to
match, and what a client that never declared the extension gets.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, TaskPolicy
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import TASKS_EXTENSION_ID
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.tasks.conftest import RecordingExecutor, slow_service
from tests.tasks.urls import EXECUTOR, SERVER

MODERN = "2026-07-28"
LEGACY = "2025-11-25"

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.urls("tests.tasks.urls")]


def _meta(*, declares: bool = True, version: str = MODERN) -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientCapabilities": {
            "extensions": {TASKS_EXTENSION_ID: {}} if declares else {}
        },
    }


def _post(
    client: Client,
    method: str,
    params: dict[str, Any],
    *,
    declares: bool = True,
    headers: dict[str, str] | None = None,
    modern: bool = True,
) -> Any:
    body: dict[str, Any] = dict(params)
    sent: dict[str, str] = {"Mcp-Method": method, "Mcp-Protocol-Version": MODERN}
    if modern:
        body["_meta"] = _meta(declares=declares)
        name = params.get("name") or params.get("taskId")
        if name is not None:
            sent["Mcp-Name"] = name
    else:
        sent["Mcp-Protocol-Version"] = LEGACY
        sent.pop("Mcp-Method")
    sent.update(headers or {})
    return client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": body}),
        content_type="application/json",
        headers={k: v for k, v in sent.items() if v is not None},
    )


def _result(response: Any) -> Any:
    return json.loads(response.content)["result"]


def _error(response: Any) -> Any:
    return json.loads(response.content)["error"]


# ----- the decision at tools/call -----


def test_a_declaring_client_gets_a_task_handle(client: Client) -> None:
    result = _result(_post(client, "tools/call", {"name": "t.optional", "arguments": {}}))
    assert result["resultType"] == "task"
    assert result["status"] == "working"
    assert result["taskId"] in EXECUTOR.enqueued


def test_the_handle_resolves_immediately_by_polling(client: Client) -> None:
    """The durability requirement, seen from outside: the id the client was
    handed works on the very next request."""
    task_id = _result(_post(client, "tools/call", {"name": "t.optional", "arguments": {}}))[
        "taskId"
    ]
    assert _result(_post(client, "tasks/get", {"taskId": task_id}))["status"] == "working"


def test_the_full_round_trip(client: Client) -> None:
    task_id = _result(_post(client, "tools/call", {"name": "t.optional", "arguments": {}}))[
        "taskId"
    ]
    SERVER.run_task(task_id)
    polled = _result(_post(client, "tasks/get", {"taskId": task_id}))
    assert polled["status"] == "completed"
    assert polled["result"]["structuredContent"] == {"done": "yes"}


def test_a_non_declaring_client_gets_the_result_inline_when_the_policy_allows(
    client: Client,
) -> None:
    """``OPTIONAL`` means a task *if it can*, and an ordinary call otherwise —
    so an older client keeps working against the same tool."""
    result = _result(
        _post(client, "tools/call", {"name": "t.optional", "arguments": {}}, declares=False)
    )
    assert result["structuredContent"] == {"done": "yes"}
    assert "taskId" not in result


def test_a_non_declaring_client_calling_a_task_only_tool_is_refused(client: Client) -> None:
    """The case the spec names: unable to service the request without returning
    a task handle the client cannot accept."""
    response = _post(client, "tools/call", {"name": "t.required", "arguments": {}}, declares=False)
    assert response.status_code == 400
    assert _error(response)["code"] == -32021


def test_a_tool_that_never_opted_in_is_unaffected(client: Client) -> None:
    result = _result(_post(client, "tools/call", {"name": "t.inline", "arguments": {}}))
    assert result["structuredContent"] == {"done": "yes"}


def test_a_legacy_client_never_gets_a_task(client: Client) -> None:
    """Not an era branch — it falls out of the shape. Legacy capabilities
    arrived once at ``initialize``, so nothing is declared *on the request*."""
    response = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": LEGACY, "capabilities": {}},
            }
        ),
        content_type="application/json",
    )
    session = response.headers["Mcp-Session-Id"]
    called = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "t.optional", "arguments": {}},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Session-Id": session, "Mcp-Protocol-Version": LEGACY},
    )
    assert _result(called)["structuredContent"] == {"done": "yes"}


# ----- routing headers -----


def test_mcp_name_must_carry_the_task_id(client: Client) -> None:
    """⚠ The extension requires it so a gateway can route a follow-up to the
    instance holding that task's state. Without the mirroring registered, every
    conformant ``tasks/*`` request would fail header validation."""
    task_id = _result(_post(client, "tools/call", {"name": "t.optional", "arguments": {}}))[
        "taskId"
    ]
    assert _post(client, "tasks/get", {"taskId": task_id}).status_code == 200


def test_a_task_id_that_disagrees_with_the_header_is_a_mismatch(client: Client) -> None:
    task_id = _result(_post(client, "tools/call", {"name": "t.optional", "arguments": {}}))[
        "taskId"
    ]
    response = _post(
        client, "tasks/get", {"taskId": task_id}, headers={"Mcp-Name": "something-else"}
    )
    assert response.status_code == 400
    assert _error(response)["code"] == -32020


# ----- advertisement -----


def test_discover_advertises_the_extension(client: Client) -> None:
    result = _result(_post(client, "server/discover", {}))
    assert result["capabilities"]["extensions"] == {TASKS_EXTENSION_ID: {}}


def test_a_server_that_runs_no_tasks_advertises_nothing() -> None:
    """A capability is a promise. Advertising one this server cannot keep would
    have a client wait for a result that never comes."""
    from rest_framework_mcp.handlers.handle_initialize import build_capabilities
    from tests.tasks.conftest import context

    bare = MCPServer(name="bare", auth_backend=AllowAnyBackend())
    bare.register_service_tool(
        name="x", description="x", spec=ServiceSpec(service=slow_service, atomic=False)
    )
    assert build_capabilities(context(bare)).extensions is None


def test_a_store_without_an_executor_does_not_advertise() -> None:
    """Half the machinery creates tasks nothing will ever run."""
    from rest_framework_mcp.handlers.handle_initialize import build_capabilities
    from tests.tasks.conftest import context

    half = MCPServer(name="half", auth_backend=AllowAnyBackend(), task_store=InMemoryTaskStore())
    half.register_service_tool(
        name="x", description="x", spec=ServiceSpec(service=slow_service, atomic=False)
    )
    assert build_capabilities(context(half)).extensions is None


def test_supplying_an_executor_is_enough_to_get_a_store() -> None:
    """The executor is the switch: a store appears, namespaced like the session
    store, so the common case is one argument rather than two."""
    from rest_framework_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore

    server = MCPServer(
        name="auto", auth_backend=AllowAnyBackend(), task_executor=RecordingExecutor()
    )
    assert isinstance(server.task_store, DjangoCacheTaskStore)


def test_passing_task_store_none_is_not_a_request_for_the_default() -> None:
    """Distinguishable from "not passed" — a way to say "I will wire the store
    later" without silently getting one."""
    server = MCPServer(
        name="deferred",
        auth_backend=AllowAnyBackend(),
        task_store=None,
        task_executor=RecordingExecutor(),
    )
    assert server.task_store is None


def test_a_required_tool_on_a_server_that_cannot_run_tasks_blames_the_server() -> None:
    """⚠ ``-32603``, not the capability error. The client's request is fine;
    the tool was declared un-runnable inline with nothing configured to run it
    elsewhere. Blaming the client would send a competent one round a loop it
    cannot win."""
    from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
    from tests.tasks.conftest import context

    server = MCPServer(name="misconfigured", auth_backend=AllowAnyBackend())
    server.register_service_tool(
        name="t.required",
        description="x",
        spec=ServiceSpec(service=slow_service, atomic=False),
        task_policy=TaskPolicy.REQUIRED,
    )
    result = handle_tools_call({"name": "t.required", "arguments": {}}, context(server))
    assert result.code == -32603
    assert "task_store=" in result.message
