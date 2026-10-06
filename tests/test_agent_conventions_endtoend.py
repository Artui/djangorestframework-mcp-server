"""``MCPServer(conventions=...)``: each line lands where it is written, and only there.

The server decides **whether** a line appears -- a tool with no handle gets no
handle line, an unpaginated tool no scope sentence -- and the conventions decide
**what it says**. These tests drive a real server through ``list_tools``,
``acall_tool`` and ``call_tool`` and compare everything it answers, so a field
reaching a place it should not, or failing to reach one it should, shows up as a
difference.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async

from rest_framework_mcp import AgentConventions, MCPServer
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.tasks.create_task import create_task
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.tasks.conftest import RecordingExecutor
from tests.testapp.conventions import REFUSED_SELECTION, conventions_server
from tests.testapp.models import Invoice
from tests.utils import tool_error

DEFAULTS = AgentConventions()

# How many times each line appears in ``_observe``: the handle wording once per
# tool (both render a handle), the scope sentence on the param and in the render
# refusal, the missing-argument message from ``acall_tool`` and ``call_tool``.
WHERE: dict[str, int] = {
    "handle_field_description": 2,
    "handle_line": 2,
    "query_param_on_pages": 2,
    "missing_arguments": 2,
}


async def _observe(server: MCPServer) -> dict[str, Any]:
    """Everything the server says that a convention could reach."""
    return {
        "listing": await sync_to_async(server.list_tools)(user=None),
        "page": await server.acall_tool("invoices.list", {}, user=None),
        "refused_selection": await server.acall_tool(
            "invoices.list", {"fields": REFUSED_SELECTION}, user=None
        ),
        "missing": await server.acall_tool("invoices.rename", {"number": "X"}, user=None),
        "missing_in_process": (
            await sync_to_async(server.call_tool)("invoices.rename", {"number": "X"}, user=None)
        ).to_dict(),
    }


def _rendered(text: str) -> str:
    """``text`` as it reads once served: the one placeholder filled for ``pk``."""
    return text.replace("{names}", "`pk`")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("field", list(WHERE))
async def test_each_field_lands_where_it_is_written_and_only_there(field: str) -> None:
    await Invoice.objects.acreate(number="INV-1")
    sentinel = f"Sentinel for {field}: {{names}}." if field == "missing_arguments" else field
    default_text = _rendered(getattr(DEFAULTS, field))

    default = json.dumps(await _observe(conventions_server()), sort_keys=True)
    custom = json.dumps(
        await _observe(conventions_server(AgentConventions(**{field: sentinel}))),
        sort_keys=True,
    )

    # Present where the table says, so the replacement below is not vacuous.
    assert default.count(default_text) == WHERE[field]
    assert custom.count(_rendered(sentinel)) == WHERE[field]
    # And nothing else moved: the custom server answers exactly what the
    # default one does, with this one line swapped.
    assert custom == default.replace(default_text, _rendered(sentinel))


@pytest.mark.django_db(transaction=True)
async def test_none_drops_the_handle_wording_and_the_scope_sentence() -> None:
    await Invoice.objects.acreate(number="INV-1")
    server = conventions_server(
        AgentConventions(handle_field_description=None, handle_line=None, query_param_on_pages=None)
    )

    seen = await _observe(server)

    tools = {tool["name"]: tool for tool in seen["listing"]["tools"]}
    assert tools["invoices.list"]["description"] == "List invoices."
    assert tools["invoices.rename"]["description"] == "Rename an invoice."
    assert "description" not in tools["invoices.rename"]["outputSchema"]["properties"]["id"]
    item = tools["invoices.list"]["outputSchema"]["properties"]["items"]["items"]
    assert "description" not in item["properties"]["id"]
    fields = tools["invoices.list"]["inputSchema"]["properties"]["fields"]
    assert fields["description"] == "Fields to return."
    assert tool_error(seen["refused_selection"])["message"] == (
        "`fields` was rejected while rendering the result: `items` field is not found."
    )


@pytest.mark.django_db(transaction=True)
async def test_two_servers_in_one_process_keep_their_own_wording() -> None:
    # Conventions are instance state: nothing is read from, or written to, the
    # module, so a second server cannot change what the first one says, in
    # either order.
    await Invoice.objects.acreate(number="INV-1")
    first = conventions_server(
        AgentConventions(handle_line="First line.", missing_arguments="First: {names}.")
    )
    second = conventions_server(
        AgentConventions(handle_line="Second line.", missing_arguments="Second: {names}.")
    )
    default = conventions_server()

    seen = [await _observe(server) for server in (first, second, default, first)]

    lines = [{tool["description"] for tool in observed["listing"]["tools"]} for observed in seen]
    assert (
        lines[0]
        == lines[3]
        == {
            "List invoices.\n\nFirst line.",
            "Rename an invoice.\n\nFirst line.",
        }
    )
    assert lines[1] == {"List invoices.\n\nSecond line.", "Rename an invoice.\n\nSecond line."}
    assert lines[2] == {
        f"List invoices.\n\n{DEFAULTS.handle_line}",
        f"Rename an invoice.\n\n{DEFAULTS.handle_line}",
    }
    messages = [tool_error(observed["missing"])["message"] for observed in seen]
    assert messages == [
        "First: `pk`.",
        "Second: `pk`.",
        "Missing required argument(s): `pk`.",
        "First: `pk`.",
    ]


@pytest.mark.django_db
def test_a_task_runs_under_its_servers_conventions() -> None:
    # A task runs off the request path, in a context the server rebuilds for
    # the worker from its own state, so that rebuild has to carry the wording
    # too or every task-run call answers in the defaults.
    Invoice.objects.create(number="INV-1")
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = conventions_server(
        AgentConventions(query_param_on_pages="Task scope.", missing_arguments="Task: {names}."),
        task_store=store,
        task_executor=executor,
    )

    def run(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        task = create_task(
            store=store,
            executor=executor,
            tool_name=tool_name,
            arguments=arguments,
            token=TokenInfo(user=None),
            ttl_ms=60_000,
            poll_interval_ms=500,
        )
        server.run_task(task.task_id)
        return tool_error(store.get(task.task_id).task.result)

    assert run("invoices.list", {"fields": REFUSED_SELECTION})["message"] == (
        "`fields` was rejected while rendering the result: `items` field is not found. Task scope."
    )
    assert run("invoices.rename", {"number": "X"})["message"] == "Task: `pk`."


def test_a_server_keeps_the_conventions_it_was_given() -> None:
    conventions = AgentConventions(handle_line="Mine.")

    assert conventions_server(conventions).conventions is conventions
    assert conventions_server().conventions == AgentConventions()
