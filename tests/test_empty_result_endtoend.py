"""A result with nothing to present conforms to MCP and to the advertised schema.

MCP requires ``structuredContent`` to be an object, and a server advertising an
``outputSchema`` MUST return structured content that conforms to it. Two
situations present nothing: a selector tool's ``allow_none`` RETRIEVE that finds
no row, and a single-row service tool whose output re-read finds none (dispatch
materializes the re-read with ``.first()``, whatever the nested spec declares).
One rule covers both: the result is served as ``{}``, and the ``outputSchema`` of
a tool that can present nothing admits ``{}`` beside a full row.

Each entry point is driven, because each once had its own answer: the sync wire
handler, the async one, and the in-process ``call_tool``.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.testing import assert_tool_result_conforms
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer


class _ArchiveInput(serializers.Serializer):
    number = serializers.CharField()


def _invoice_by_number(*, number: str) -> Any:
    return Invoice.objects.filter(number=number)


def _archive(*, data: dict[str, Any]) -> Invoice:
    invoice = Invoice.objects.get(number=data["number"])
    invoice.sent = True
    invoice.save(update_fields=["sent"])
    return invoice


def _unsent_only(*, instance: Invoice) -> Any:
    # The re-read filters out the row the service just changed.
    return Invoice.objects.filter(pk=instance.pk, sent=False)


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="invoices.find",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice_by_number,
            output_serializer=InvoiceOutputSerializer,
            allow_none=True,
            permission_classes=[AllowAny],
        ),
    )
    server.register_selector_tool(
        name="invoices.get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice_by_number,
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[AllowAny],
        ),
    )
    archive = ServiceSpec(
        service=_archive,
        atomic=False,
        input_serializer=_ArchiveInput,
        permission_classes=[AllowAny],
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_unsent_only,
            output_serializer=InvoiceOutputSerializer,
        ),
    )
    server.register_service_tool(name="invoices.archive", spec=archive)
    server.register_chain_tool(
        name="invoices.archive_chain",
        steps=[ChainStep("archive", archive, inputs=lambda ctx: {"data": ctx.args})],
        permissions=[],
    )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    request = HttpRequest()
    request.user = None
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _tool(server: MCPServer, name: str) -> dict[str, Any]:
    listed: Any = handle_tools_list(None, _ctx(server))
    return next(t for t in listed["tools"] if t["name"] == name)


def _wire(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    out: Any = handle_tools_call({"name": name, "arguments": arguments}, _ctx(server))
    return out


def _in_process(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return server.call_tool(name, arguments, user=None).to_dict()


def _async_wire(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    out: Any = async_to_sync(server.acall_tool)(name, arguments, user=None)
    return out


_ENTRY_POINTS = pytest.mark.parametrize(
    "call", [_wire, _in_process, _async_wire], ids=["wire", "call_tool", "acall_tool"]
)


def _assert_empty_and_conforming(tool: dict[str, Any], result: dict[str, Any]) -> None:
    assert not result.get("isError")
    # MCP: structured content is an object, and the text block carries the
    # same JSON.
    assert result["structuredContent"] == {}
    assert result["content"][0]["text"] == "{}"
    assert_tool_result_conforms(tool, result)


@_ENTRY_POINTS
@pytest.mark.django_db(transaction=True)
def test_an_allow_none_selector_miss_is_an_empty_object_its_schema_admits(call: Any) -> None:
    server = _server()

    result = call(server, "invoices.find", {"number": "nope"})

    _assert_empty_and_conforming(_tool(server, "invoices.find"), result)


@_ENTRY_POINTS
@pytest.mark.django_db(transaction=True)
def test_a_service_reread_that_finds_nothing_is_an_empty_object_its_schema_admits(
    call: Any,
) -> None:
    server = _server()
    Invoice.objects.create(number="INV-1")

    result = call(server, "invoices.archive", {"number": "INV-1"})

    _assert_empty_and_conforming(_tool(server, "invoices.archive"), result)


@pytest.mark.django_db
def test_a_chain_whose_output_step_presents_nothing_is_an_empty_object() -> None:
    server = _server()
    Invoice.objects.create(number="INV-1")

    result = _wire(server, "invoices.archive_chain", {"number": "INV-1"})

    _assert_empty_and_conforming(_tool(server, "invoices.archive_chain"), result)


@pytest.mark.django_db
def test_a_found_row_still_conforms_and_a_partial_row_does_not() -> None:
    server = _server()
    Invoice.objects.create(number="INV-1")
    tool = _tool(server, "invoices.find")

    result = _wire(server, "invoices.find", {"number": "INV-1"})

    assert result["structuredContent"]["number"] == "INV-1"
    assert_tool_result_conforms(tool, result)
    with pytest.raises(AssertionError):
        # Non-empty but missing the required ``number``: neither branch admits it.
        assert_tool_result_conforms(tool, {"structuredContent": {"amount_cents": 5}})


def test_a_retrieve_that_cannot_present_nothing_keeps_its_schema_strict() -> None:
    # A miss on a RETRIEVE without ``allow_none`` is an ``isError`` result, so
    # its schema has no reason to admit ``{}``.
    tool = _tool(_server(), "invoices.get")

    with pytest.raises(AssertionError):
        assert_tool_result_conforms(tool, {"structuredContent": {}})
