"""``tools/list`` coverage for chain tools — input/output schema emission."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from tests.testapp.serializers import InvoiceInputSerializer, InvoiceOutputSerializer


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _out_step() -> ChainStep:
    return ChainStep(
        "made",
        ServiceSpec(
            service=lambda **_: {},
            atomic=False,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer
            ),
        ),
    )


def test_chain_tool_advertises_explicit_input_schema_and_output_schema() -> None:
    server = _server()
    server.register_chain_tool(
        name="chain",
        input_serializer=InvoiceInputSerializer,
        include_output_schema=True,
        steps=[_out_step()],
    )
    out: Any = handle_tools_list(None, _ctx(server))
    tool = out["tools"][0]
    assert set(tool["inputSchema"]["properties"]) == {"number", "amount_cents"}
    assert "outputSchema" in tool
    # ``id`` is read-only on the ModelSerializer, so it's excluded from the schema.
    assert set(tool["outputSchema"]["properties"]) == {"number", "amount_cents", "sent"}


def test_chain_tool_falls_back_to_first_step_input_schema() -> None:
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                "made",
                ServiceSpec(
                    service=lambda **_: {},
                    atomic=False,
                    input_serializer=InvoiceInputSerializer,
                ),
            )
        ],
    )
    out: Any = handle_tools_list(None, _ctx(server))
    tool = out["tools"][0]
    # No explicit chain input_serializer → first step's schema is advertised.
    assert set(tool["inputSchema"]["properties"]) == {"number", "amount_cents"}
