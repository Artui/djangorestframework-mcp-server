"""Async sibling for selector-tool dispatch — exercises the async handler path.

Mirrors a focused subset of the sync tests; full per-pipeline coverage
lives in ``test_selector_tool_dispatch.py``.
"""

from __future__ import annotations

from typing import Any

import django_filters
import pytest
from django.http import HttpRequest
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
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


class InvoiceFilterSet(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()

    class Meta:
        model = Invoice
        fields = ["sent"]


@pytest.mark.django_db(transaction=True)
async def test_async_filter_then_paginate() -> None:
    """Async dispatch runs the same filter/paginate pipeline as sync."""
    from asgiref.sync import sync_to_async

    @sync_to_async
    def _setup() -> None:
        Invoice.objects.create(number="A", amount_cents=100, sent=True)
        Invoice.objects.create(number="B", amount_cents=200, sent=False)
        Invoice.objects.create(number="C", amount_cents=300, sent=True)
        Invoice.objects.create(number="D", amount_cents=400, sent=True)

    await _setup()

    server = _server()

    def list_invoices() -> Any:
        return Invoice.objects.all()

    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=list_invoices, output_serializer=InvoiceOutputSerializer),
        filter_set=InvoiceFilterSet,
        ordering_fields=["amount_cents"],
        paginate=True,
    )

    out = await handle_tools_call_async(
        {
            "name": "invoices.list",
            "arguments": {"sent": True, "ordering": "amount_cents", "page": 1, "limit": 2},
        },
        _ctx(server),
    )
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert payload["page"] == 1
    assert payload["totalPages"] == 2
    assert [item["number"] for item in payload["items"]] == ["A", "C"]


async def test_async_translates_service_validation_error() -> None:
    from rest_framework_services.exceptions.service_validation_error import (
        ServiceValidationError,
    )

    server = _server()

    def selector() -> None:
        raise ServiceValidationError({"f": ["bad"]})

    server.register_selector_tool(name="x", spec=SelectorSpec(selector=selector))
    out = await handle_tools_call_async({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602
    assert out.data == {"detail": {"f": ["bad"]}}


async def test_async_translates_service_error_with_recording(settings) -> None:
    """``RECORD_SERVICE_EXCEPTIONS=True`` on the async path."""
    from rest_framework_services.exceptions.service_error import ServiceError

    settings.REST_FRAMEWORK_MCP = {"RECORD_SERVICE_EXCEPTIONS": True}
    server = _server()

    def selector() -> None:
        raise ServiceError("nope")

    server.register_selector_tool(name="x", spec=SelectorSpec(selector=selector))
    out = await handle_tools_call_async({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32000


async def test_async_translates_service_error_without_recording() -> None:
    """``ServiceError`` on the async path without OTel recording (default)."""
    from rest_framework_services.exceptions.service_error import ServiceError

    server = _server()

    def selector() -> None:
        raise ServiceError("nope")

    server.register_selector_tool(name="x", spec=SelectorSpec(selector=selector))
    out = await handle_tools_call_async({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32000


async def test_async_input_serializer_rejects_invalid() -> None:
    from rest_framework import serializers as drf_serializers

    class _Args(drf_serializers.Serializer):
        flag = drf_serializers.BooleanField()

    server = _server()

    def selector(*, data: Any) -> list[Any]:  # noqa: ARG001
        return []

    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(selector=selector),
        input_serializer=_Args,
    )
    out = await handle_tools_call_async(
        {"name": "x", "arguments": {"flag": "not-a-bool"}}, _ctx(server)
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


async def test_async_denies_on_permission() -> None:
    class _Deny:
        def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
            return False

        def required_scopes(self) -> list[str]:
            return ["x"]

    server = _server()

    def selector() -> list[Any]:
        return []

    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(selector=selector),
        permissions=[_Deny()],
    )
    out = await handle_tools_call_async({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32002
