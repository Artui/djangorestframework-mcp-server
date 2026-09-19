"""An unavailable tool, on the wire: left out of ``tools/list``, refused by ``tools/call``.

Through the real URL conf, in both eras and on both transports, because the two
transports are parallel implementations and the async one reaches the list
handler through an executor hop the sync one does not make.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.test import AsyncClient, Client, override_settings
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


def _server() -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    for name, met in (("open_books", True), ("close_books", False)):
        server.register_service_tool(
            name=name,
            spec=ServiceSpec(
                service=lambda **_: {"status": "ran"},
                atomic=False,
                affordances=[
                    Affordance(
                        code="books_closed",
                        reason="The books are closed.",
                        when=lambda met=met: met,
                    )
                ],
            ),
            description=name,
            permissions=[],
        )
    return server


async def _awaited(response: Any) -> Any:
    return await response


class _Wire:
    """One client speaking one era to one transport."""

    def __init__(self, *, era: str, is_async: bool) -> None:
        self.era = era
        self.is_async = is_async
        self.client: Any = AsyncClient() if is_async else Client()
        self.session_id: str | None = None

    def post(self, method: str, params: dict[str, Any]) -> Any:
        headers: dict[str, str] = {"Mcp-Protocol-Version": self.era}
        body_params: dict[str, Any] = dict(params)
        if self.era == MODERN:
            headers["Mcp-Method"] = method
            if method == "tools/call":
                headers["Mcp-Name"] = params["name"]
            body_params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": MODERN,
                "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        elif method == "initialize":
            del headers["Mcp-Protocol-Version"]
        elif self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        response = self.client.post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": body_params}),
            content_type="application/json",
            headers=headers,
        )
        # The async client hands back a coroutine; awaiting it on a private loop
        # keeps one test body for both transports.
        return async_to_sync(_awaited)(response) if self.is_async else response

    def open(self) -> None:
        if self.era == LEGACY:
            response = self.post(
                "initialize",
                {
                    "protocolVersion": LEGACY,
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            )
            assert response.status_code == 200, response.content
            self.session_id = response["Mcp-Session-Id"]


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("era", [LEGACY, MODERN], ids=["legacy", "modern"])
@pytest.mark.django_db(transaction=True)
def test_an_unavailable_tool_is_left_out_and_refused_with_its_code(
    era: str, is_async: bool
) -> None:
    server = _server()
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        wire = _Wire(era=era, is_async=is_async)
        wire.open()
        listed = wire.post("tools/list", {})
        called = wire.post("tools/call", {"name": "close_books", "arguments": {}})

    assert listed.status_code == 200, listed.content
    listing: Any = json.loads(listed.content)["result"]
    assert [tool["name"] for tool in listing["tools"]] == ["open_books"]
    assert listing["cacheScope"] == "private"

    assert called.status_code == 200, called.content
    result: Any = json.loads(called.content)["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"] == {
        "type": "service_error",
        "message": "The books are closed.",
        "code": "books_closed",
    }
