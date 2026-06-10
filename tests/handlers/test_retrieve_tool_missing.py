"""RETRIEVE selector tools: missing-row handling + ``allow_none`` parity."""

from __future__ import annotations

import pytest
from django.db.models import QuerySet
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.utils import tool_error


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


class _InvoiceOut(drf_serializers.Serializer):
    number = drf_serializers.CharField()


def _get_invoice(*, pk: int) -> Invoice | None:
    return Invoice.objects.filter(pk=pk).first()


def _invoice_queryset(*, pk: int) -> QuerySet[Invoice]:
    return Invoice.objects.filter(pk=pk)


def _register(server: MCPServer, selector, *, allow_none: bool = False) -> None:
    server.register_selector_tool(
        name="invoices.get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            output_serializer=_InvoiceOut,
            allow_none=allow_none,
        ),
    )


@pytest.mark.django_db
def test_missing_row_is_not_found_by_default() -> None:
    server = _server()
    _register(server, _get_invoice)
    out = handle_tools_call({"name": "invoices.get", "arguments": {"pk": 99999}}, _ctx(server))
    assert tool_error(out)["type"] == "not_found"


@pytest.mark.django_db
def test_allow_none_returns_null_result() -> None:
    server = _server()
    _register(server, _get_invoice, allow_none=True)
    out = handle_tools_call({"name": "invoices.get", "arguments": {"pk": 99999}}, _ctx(server))
    assert isinstance(out, dict)
    assert "isError" not in out
    assert "structuredContent" not in out
    assert out["content"][0]["text"] == "null"


@pytest.mark.django_db
def test_queryset_return_is_materialized() -> None:
    invoice = Invoice.objects.create(number="n-1")
    server = _server()
    _register(server, _invoice_queryset)
    out = handle_tools_call({"name": "invoices.get", "arguments": {"pk": invoice.pk}}, _ctx(server))
    assert "isError" not in out
    assert out["structuredContent"] == {"number": "n-1"}


@pytest.mark.django_db
def test_empty_queryset_is_not_found() -> None:
    server = _server()
    _register(server, _invoice_queryset)
    out = handle_tools_call({"name": "invoices.get", "arguments": {"pk": 99999}}, _ctx(server))
    assert tool_error(out)["type"] == "not_found"


def _strict_get(*, pk: int) -> Invoice:
    return Invoice.objects.get(pk=pk)


@pytest.mark.django_db
def test_does_not_exist_is_not_found() -> None:
    server = _server()
    _register(server, _strict_get)
    out = handle_tools_call({"name": "invoices.get", "arguments": {"pk": 99999}}, _ctx(server))
    assert tool_error(out)["type"] == "not_found"


@pytest.mark.django_db
def test_does_not_exist_with_allow_none_returns_null() -> None:
    server = _server()
    _register(server, _strict_get, allow_none=True)
    out = handle_tools_call({"name": "invoices.get", "arguments": {"pk": 99999}}, _ctx(server))
    assert "isError" not in out
    assert out["content"][0]["text"] == "null"


@pytest.mark.django_db(transaction=True)
async def test_async_does_not_exist_is_not_found() -> None:
    server = _server()
    _register(server, _strict_get)
    out = await handle_tools_call_async(
        {"name": "invoices.get", "arguments": {"pk": 99999}}, _ctx(server)
    )
    assert tool_error(out)["type"] == "not_found"


@pytest.mark.django_db(transaction=True)
async def test_async_missing_row_with_allow_none_returns_null() -> None:
    server = _server()
    _register(server, _get_invoice, allow_none=True)
    out = await handle_tools_call_async(
        {"name": "invoices.get", "arguments": {"pk": 99999}}, _ctx(server)
    )
    assert "isError" not in out
    assert out["content"][0]["text"] == "null"


@pytest.mark.django_db
def test_list_selector_does_not_exist_propagates() -> None:
    def exploding_list() -> QuerySet[Invoice]:
        raise Invoice.DoesNotExist

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST, selector=exploding_list, output_serializer=_InvoiceOut
        ),
    )
    with pytest.raises(Invoice.DoesNotExist):
        handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))


@pytest.mark.django_db(transaction=True)
async def test_async_list_selector_does_not_exist_propagates() -> None:
    def exploding_list() -> QuerySet[Invoice]:
        raise Invoice.DoesNotExist

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST, selector=exploding_list, output_serializer=_InvoiceOut
        ),
    )
    with pytest.raises(Invoice.DoesNotExist):
        await handle_tools_call_async({"name": "invoices.list", "arguments": {}}, _ctx(server))
