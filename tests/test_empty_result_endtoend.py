"""A result with nothing to present conforms to MCP and to the advertised schema.

MCP requires ``structuredContent`` to be an object, and a server advertising an
``outputSchema`` MUST return structured content that conforms to it. Two
situations present nothing: a selector tool's ``allow_none`` RETRIEVE that finds
no row, and a single-row service tool whose output re-read selector finds none
(dispatch materializes the re-read with ``.first()``, whatever the nested spec
declares). One rule covers both: the result is served as ``{}``, and the
``outputSchema`` of a tool that can present nothing admits ``{}`` beside a full
row. Every other tool keeps a strict ``required``, because loosening it turns
every row field optional for a client generating types from the schema.

Each entry point is driven, because each once had its own answer: the sync wire
handler, the async one, and the in-process ``call_tool``. The HTTP viewsets are
driven as well, sync and async in both eras, because what a client validates is
the schema and the result read off the wire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings
from jsonschema import Draft202012Validator
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
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer
from tests.testapp.urlconf_for import urlconf_for

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


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


def _void(*, data: dict[str, Any]) -> None:
    return None


def _touch_tasks() -> None:
    # A service with nothing to return, and no re-read to present instead.
    return None


@dataclass
class _AddInput:
    a: int
    b: int


class _SumOutput(serializers.Serializer):
    result = serializers.IntegerField()


def _add(*, data: _AddInput) -> dict[str, int]:
    return {"result": data.a + data.b}


def _no_reread() -> SelectorSpec:
    # An output spec naming only the serializer: the service's own return
    # renders, with no selector to filter it away.
    return SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer)


def _server(session_store: Any = None) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=session_store)
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
    # The same service with no re-read selector: it returns the row it changed.
    send = ServiceSpec(
        service=_archive,
        atomic=False,
        input_serializer=_ArchiveInput,
        permission_classes=[AllowAny],
        output_selector_spec=_no_reread(),
    )
    server.register_service_tool(name="invoices.send", spec=send)
    server.register_chain_tool(
        name="invoices.send_chain",
        steps=[ChainStep("send", send, inputs=lambda ctx: {"data": ctx.args})],
        permissions=[],
    )
    server.register_service_tool(
        name="invoices.void",
        spec=ServiceSpec(
            service=_void,
            atomic=False,
            input_serializer=_ArchiveInput,
            permission_classes=[AllowAny],
            output_selector_spec=_no_reread(),
        ),
    )
    # The remedy the docs give for the limit above: a re-read selector handing
    # the service's return straight back, which makes the schema admit ``{}``.
    server.register_service_tool(
        name="invoices.void_reread",
        spec=ServiceSpec(
            service=_void,
            atomic=False,
            input_serializer=_ArchiveInput,
            permission_classes=[AllowAny],
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, result: result,
                output_serializer=InvoiceOutputSerializer,
            ),
        ),
    )
    # The declaration for the same case: ``allow_none=True`` says the service
    # may present nothing, so its schema admits ``{}`` with no re-read to lean on.
    touch = ServiceSpec(
        service=_touch_tasks,
        atomic=False,
        allow_none=True,
        permission_classes=[AllowAny],
        output_selector_spec=_no_reread(),
    )
    server.register_service_tool(name="tasks.touch", spec=touch)
    server.register_chain_tool(
        name="tasks.touch_chain", steps=[ChainStep("touch", touch)], permissions=[]
    )
    # django-ag-ui's typed bridge fixture, reproduced: its code-mode stub reads
    # this schema as the tool's return type.
    server.register_service_tool(
        name="sums.add",
        spec=ServiceSpec(
            service=_add,
            atomic=False,
            input_serializer=_AddInput,
            permission_classes=[AllowAny],
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=_SumOutput
            ),
        ),
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


@_ENTRY_POINTS
@pytest.mark.django_db(transaction=True)
def test_a_service_returning_none_through_a_pass_through_reread_conforms(call: Any) -> None:
    server = _server()

    result = call(server, "invoices.void_reread", {"number": "x"})

    _assert_empty_and_conforming(_tool(server, "invoices.void_reread"), result)


def _assert_admits_nothing(tool: dict[str, Any], result: dict[str, Any]) -> None:
    # Named outright as well as validated: the schema keeps its root object and
    # moves ``required`` into the ``anyOf`` beside the empty object.
    assert "required" not in tool["outputSchema"]
    assert tool["outputSchema"]["anyOf"][1] == {"maxProperties": 0}
    _assert_empty_and_conforming(tool, result)


@_ENTRY_POINTS
@pytest.mark.django_db(transaction=True)
def test_a_service_declaring_allow_none_serves_an_empty_object_its_schema_admits(
    call: Any,
) -> None:
    # ``tasks.touch`` has no re-read, so nothing but the declaration says it
    # may present nothing. ``invoices.void`` is a service returning ``None``
    # undeclared, and keeps its schema strict (below).
    server = _server()

    result = call(server, "tasks.touch", {})

    _assert_admits_nothing(_tool(server, "tasks.touch"), result)


@pytest.mark.django_db
def test_a_chain_whose_output_step_declares_allow_none_admits_an_empty_object() -> None:
    # A chain's output step is judged as the same spec registered as a tool.
    server = _server()

    result = _wire(server, "tasks.touch_chain", {})

    _assert_admits_nothing(_tool(server, "tasks.touch_chain"), result)


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


# ---------- tools that cannot present nothing keep a strict schema ----------


@pytest.mark.parametrize(
    "name", ["invoices.send", "invoices.send_chain", "invoices.void", "sums.add"]
)
def test_a_service_with_no_output_reread_keeps_its_schema_strict(name: str) -> None:
    # With no re-read selector the service's own return renders, so nothing
    # filters a row away. ``invoices.void`` returns ``None`` regardless, and is
    # served ``{}`` against this schema: the documented limit, not admitted
    # here, because admitting it would loosen every typed service's schema.
    schema = _tool(_server(), name)["outputSchema"]

    assert "anyOf" not in schema
    assert schema["required"]
    assert not Draft202012Validator(schema).is_valid({})


@_ENTRY_POINTS
@pytest.mark.django_db(transaction=True)
def test_an_undeclared_none_is_still_served_empty_against_a_strict_schema(call: Any) -> None:
    # The limit the declaration answers: drf-services presents a ``None`` the
    # spec does not declare rather than refusing it, so the call succeeds with
    # ``{}``, and a client validating it against the strict schema rejects it.
    server = _server()

    result = call(server, "invoices.void", {"number": "x"})

    assert not result.get("isError")
    assert result["structuredContent"] == {}
    with pytest.raises(AssertionError):
        assert_tool_result_conforms(_tool(server, "invoices.void"), result)


def test_a_typed_service_with_no_reread_lists_the_exact_strict_schema() -> None:
    assert _tool(_server(), "sums.add")["outputSchema"] == {
        "type": "object",
        "properties": {"result": {"type": "integer"}},
        "required": ["result"],
    }


@pytest.mark.parametrize("name", ["invoices.find", "invoices.archive", "invoices.archive_chain"])
def test_a_tool_that_can_present_nothing_moves_required_into_the_any_of(name: str) -> None:
    schema = _tool(_server(), name)["outputSchema"]

    assert "required" not in schema
    assert schema["anyOf"][1] == {"maxProperties": 0}
    assert Draft202012Validator(schema).is_valid({})


# ---------- what the HTTP viewsets serve ----------


async def _awaited(response: Any) -> Any:
    return await response


def _send(
    client: Any, is_async: bool, method: str, params: dict[str, Any], headers: dict[str, str]
) -> Any:
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
        content_type="application/json",
        headers=headers,
    )
    return async_to_sync(_awaited)(response) if is_async else response


def _post(era: str, is_async: bool, method: str, params: dict[str, Any]) -> Any:
    """One request to the mounted endpoint, in ``era``, on the chosen viewset."""
    client: Any = AsyncClient() if is_async else Client()
    headers: dict[str, str] = {"Mcp-Protocol-Version": era}
    body: dict[str, Any] = dict(params)
    if era == MODERN:
        headers["Mcp-Method"] = method
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        body["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    else:
        opened = _send(
            client,
            is_async,
            "initialize",
            {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
            {},
        )
        assert opened.status_code == 200, opened.content
        headers["Mcp-Session-Id"] = opened["Mcp-Session-Id"]
    response = _send(client, is_async, method, body, headers)
    assert response.status_code == 200, response.content
    return json.loads(response.content)["result"]


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("era", [LEGACY, MODERN], ids=["legacy", "modern"])
@pytest.mark.django_db(transaction=True)
def test_every_served_result_conforms_to_its_served_schema(era: str, is_async: bool) -> None:
    server = _server(session_store=InMemorySessionStore())
    Invoice.objects.create(number="INV-1")
    Invoice.objects.create(number="INV-2")
    calls: list[tuple[str, dict[str, Any], Any]] = [
        # An ``allow_none`` miss, and a re-read that filters out the row the
        # service just archived: both nothing, both served ``{}``.
        ("invoices.find", {"number": "nope"}, {}),
        ("invoices.archive", {"number": "INV-1"}, {}),
        # A service declaring ``allow_none=True`` that returns ``None``.
        ("tasks.touch", {}, {}),
        # A service with no re-read returns the row it changed, against a
        # schema that still requires the row's fields.
        ("invoices.send", {"number": "INV-2"}, "INV-2"),
    ]
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        listed = _post(era, is_async, "tools/list", {})["tools"]
        schemas = {tool["name"]: tool["outputSchema"] for tool in listed}
        for name, arguments, expected in calls:
            result = _post(era, is_async, "tools/call", {"name": name, "arguments": arguments})

            assert not result.get("isError"), result
            served = result["structuredContent"]
            if expected == {}:
                assert served == {}
            else:
                assert served["number"] == expected
                assert "required" in schemas[name]
            # Draft 2020-12, the dialect MCP names for a schema with no ``$schema``.
            Draft202012Validator(schemas[name]).validate(served)
