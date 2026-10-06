"""A server's ``conventions=`` reaches the wire through both viewsets, in both eras.

Over HTTP the handlers never see the server: each viewset builds the request's
context from what ``MCPServer`` handed ``as_view``, in two places, one per
protocol era. A convention dropped at any of those four sites leaves that route
speaking the defaults while ``call_tool`` and ``list_tools`` speak the server's,
which is why each viewset is driven here in both eras rather than once.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import AsyncClient, Client, override_settings

from rest_framework_mcp import AgentConventions
from tests.testapp.conventions import conventions_server
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import tool_error

LEGACY = "2025-11-25"
MODERN = "2026-07-28"

CONVENTIONS = AgentConventions(
    handle_field_description="Wire handle.",
    handle_line="Wire line.",
    query_param_on_pages="Wire scope.",
    missing_arguments="Wire missing: {names}.",
)


def _initialize_body() -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.0"},
            },
        }
    )


def _request(
    era: str, method: str, params: dict[str, Any], session_id: str | None
) -> dict[str, Any]:
    """The keyword arguments of a POST carrying ``method`` in ``era``."""
    headers: dict[str, str] = {"Mcp-Protocol-Version": era}
    if era == MODERN:
        params = {
            **params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MODERN,
                "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0.0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        }
        headers["Mcp-Method"] = method
        if "name" in params:
            headers["Mcp-Name"] = params["name"]
    else:
        assert session_id is not None
        headers["Mcp-Session-Id"] = session_id
    return {
        "data": json.dumps({"jsonrpc": "2.0", "id": 2, "method": method, "params": params}),
        "content_type": "application/json",
        "headers": headers,
    }


_MISSING_PK: dict[str, Any] = {"name": "invoices.rename", "arguments": {"number": "X"}}


def _assert_served_with_the_conventions(listed: Any, called: Any) -> None:
    assert listed.status_code == 200, listed.content
    tools = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
    rename = tools["invoices.rename"]
    assert rename["description"] == "Rename an invoice.\n\nWire line."
    assert rename["outputSchema"]["properties"]["id"]["description"] == "Wire handle."
    fields = tools["invoices.list"]["inputSchema"]["properties"]["fields"]
    assert fields["description"] == "Fields to return. Wire scope."
    assert called.status_code == 200, called.content
    assert tool_error(called.json()["result"])["message"] == "Wire missing: `pk`."


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("era", [LEGACY, MODERN])
def test_the_sync_viewset_serves_the_servers_conventions(client: Client, era: str) -> None:
    with override_settings(ROOT_URLCONF=urlconf_for(conventions_server(CONVENTIONS))):
        session_id: str | None = None
        if era == LEGACY:
            opened = client.post("/mcp/", data=_initialize_body(), content_type="application/json")
            session_id = opened["Mcp-Session-Id"]
        listed = client.post("/mcp/", **_request(era, "tools/list", {}, session_id))
        called = client.post("/mcp/", **_request(era, "tools/call", _MISSING_PK, session_id))

    _assert_served_with_the_conventions(listed, called)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("era", [LEGACY, MODERN])
async def test_the_async_viewset_serves_the_servers_conventions(era: str) -> None:
    client = AsyncClient()
    server = conventions_server(CONVENTIONS)
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        session_id: str | None = None
        if era == LEGACY:
            opened = await client.post(
                "/mcp/", data=_initialize_body(), content_type="application/json"
            )
            session_id = opened["Mcp-Session-Id"]
        listed = await client.post("/mcp/", **_request(era, "tools/list", {}, session_id))
        called = await client.post("/mcp/", **_request(era, "tools/call", _MISSING_PK, session_id))

    _assert_served_with_the_conventions(listed, called)
