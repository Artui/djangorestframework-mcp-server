"""Agent audience, on the wire.

Unit coverage proves each piece; this proves the wiring. A tool registered with
a marked-up serializer must actually *return* a projected payload and advertise
a schema that matches it — a round trip through the handlers, not through the
projection helpers directly.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework_services import SelectorKind, SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from tests.testapp.models import Invoice
from tests.testapp.serializers import AgentInvoiceSerializer


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="list_invoices",
        description="List invoices.",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda **_: Invoice.objects.all().order_by("id"),
            output_serializer=AgentInvoiceSerializer,
        ),
        permissions=[],
    )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


@pytest.mark.django_db
def test_the_returned_payload_is_projected() -> None:
    Invoice.objects.create(number="FV/2026/0043", amount_cents=124000, sent=True)
    server = _server()

    result: Any = handle_tools_call({"name": "list_invoices", "arguments": {}}, _ctx(server))
    rows = result["structuredContent"]

    assert rows == [
        {
            "id": 1,
            "number": "FV/2026/0043",
            "amount_cents": 124000,
            "status": "Awaiting review",
        }
    ]
    # The text block a client reads carries the same projection, not the raw row.
    assert "sent" not in result["content"][0]["text"]
    assert json.loads(result["content"][0]["text"]) == rows


@pytest.mark.django_db
def test_the_advertised_schema_matches_what_the_call_returns() -> None:
    Invoice.objects.create(number="FV/2026/0043", amount_cents=124000, sent=True)
    server = _server()
    ctx = _ctx(server)

    tool: Any = handle_tools_list(None, ctx)["tools"][0]
    returned: Any = handle_tools_call({"name": "list_invoices", "arguments": {}}, ctx)

    advertised = set(tool["outputSchema"]["items"]["properties"])
    assert advertised == set(returned["structuredContent"][0])
    # The read-only primary key is described rather than silently omitted.
    assert "id" in advertised


@pytest.mark.django_db
def test_the_description_teaches_the_handle_convention() -> None:
    tool: Any = handle_tools_list(None, _ctx(_server()))["tools"][0]

    assert tool["description"].startswith("List invoices.")
    assert "Identify records by `number`." in tool["description"]
    assert tool["outputSchema"]["items"]["properties"]["id"]["description"] == "Invoice handle."
