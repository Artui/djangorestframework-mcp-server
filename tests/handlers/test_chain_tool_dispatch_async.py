"""Async parity for chain-tool dispatch."""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _create(*, number: str, amount_cents: int) -> Invoice:
    return Invoice.objects.create(number=number, amount_cents=amount_cents)


@pytest.mark.django_db(transaction=True)
async def test_async_chain_happy_path() -> None:
    @sync_to_async
    def _setup() -> int:
        return Invoice.objects.create(number="A", amount_cents=100).pk

    src_pk = await _setup()

    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                "src",
                SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda: Invoice.objects.get(pk=src_pk),
                ),
            ),
            ChainStep(
                "copy",
                ServiceSpec(
                    service=_create,
                    atomic=False,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer
                    ),
                ),
                inputs=lambda ctx: {"number": "B", "amount_cents": ctx["src"].amount_cents},
            ),
        ],
    )
    out = await handle_tools_call_async({"name": "chain", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["structuredContent"]["number"] == "B"
    assert out["structuredContent"]["amount_cents"] == 100


@pytest.mark.django_db(transaction=True)
async def test_async_chain_atomic_rollback() -> None:
    server = _server()

    def _boom(**_: Any) -> None:
        raise ServiceError("kaboom")

    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                "first",
                ServiceSpec(service=_create, atomic=False),
                inputs=lambda ctx: {"number": "A", "amount_cents": 1},
            ),
            ChainStep("second", ServiceSpec(service=_boom, atomic=False)),
        ],
    )
    out = await handle_tools_call_async({"name": "chain", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.SERVER_ERROR
    assert out.data == {"failedStep": "second"}

    @sync_to_async
    def _count() -> int:
        return Invoice.objects.count()

    assert await _count() == 0
