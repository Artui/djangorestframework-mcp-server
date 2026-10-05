"""Argument validation on ``tools/call`` is a tool execution error, not ``-32602``.

The MCP spec's tools "Error Handling" section lists "Input validation errors
(e.g., date in wrong format, value out of range)" under *tool execution
errors*, reported in the result with ``isError: true`` so a model can read them
and correct itself (2025-11-25, 2026-07-28; 2025-06-18 lists "Invalid input
data" there too). Protocol errors are kept for an unknown tool and a request
that fails the ``CallToolRequest`` schema itself.

So every way a tool's *arguments* can be refused -- an unexpected argument
under ``UnknownArguments.REJECT``, an ``input_serializer`` rejection, a filter
value the spec's ``FilterSet`` refuses -- answers as the ``validation_error``
result a ``ServiceValidationError`` already produced, on every tool kind and
entry point. The field-keyed detail the ``-32602`` envelope carried under
``data.detail`` is the result's ``error.detail``, with the message it had
("Invalid arguments").

The unknown-tool and non-object-``arguments`` cases at the bottom are the
other half of the boundary: they stay JSON-RPC ``-32602``.
"""

from __future__ import annotations

import json
from typing import Any

import django_filters
import pytest
from django.http import HttpRequest
from django.test import Client
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode, UnknownArguments
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.conftest import post_jsonrpc
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceInputSerializer, InvoiceOutputSerializer
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import tool_error

MODERN = "2026-07-28"


class _KnownOnly(drf_serializers.Serializer):
    """A selector tool's MCP-side ``input_serializer``: one strictly-typed field."""

    known = drf_serializers.IntegerField()


class _OrderedInvoices(django_filters.FilterSet):
    """A filter whose choices are published, so a value outside them is refused."""

    ordering = django_filters.OrderingFilter(fields=(("amount_cents", "amount"),))

    class Meta:
        model = Invoice
        fields: list[str] = []


def _echo(*, data: Any) -> dict[str, Any]:
    return dict(data)


def _list_invoices(**_kwargs: Any) -> Any:
    return Invoice.objects.all()


def _server() -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_service_tool(
        name="service",
        spec=ServiceSpec(service=_echo, input_serializer=InvoiceInputSerializer, atomic=False),
    )
    server.register_selector_tool(
        name="selector",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list_invoices),
        input_serializer=_KnownOnly,
        unknown_arguments=UnknownArguments.REJECT,
        paginate=True,
    )
    server.register_selector_tool(
        name="filtered",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
            filter_set=_OrderedInvoices,
        ),
        paginate=True,
    )
    server.register_chain_tool(
        name="chain",
        input_serializer=InvoiceInputSerializer,
        steps=[ChainStep("made", ServiceSpec(service=_echo, atomic=False))],
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


async def _call(server: MCPServer, name: str, arguments: Any, *, is_async: bool) -> Any:
    params: dict[str, Any] = {"name": name, "arguments": arguments}
    if is_async:
        return await handle_tools_call_async(params, _ctx(server))
    return handle_tools_call(params, _ctx(server))


def _assert_invalid_arguments(out: Any) -> dict[str, Any]:
    """The result a refused argument earns: ``validation_error``, keyed detail."""
    assert not isinstance(out, JsonRpcError), (
        f"argument validation answered as a protocol error: {out!r}"
    )
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["message"] == "Invalid arguments"
    return error["detail"]


# ---------- an unexpected argument ----------


# One case per site that answered ``-32602``: the service tool's dispatch
# (drf-services' ``REJECT``), the selector tool's own ``input_serializer`` pass,
# and the chain's. ``is_async`` covers the async handler's own service arm, which
# is a separate ``except``; the selector and chain siblings share theirs.
@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("service", {"number": "A", "amount_cents": 1, "rogue": 1}),
        ("selector", {"known": 1, "rogue": 1}),
        ("chain", {"number": "A", "amount_cents": 1, "rogue": 1}),
    ],
)
async def test_an_unexpected_argument_is_a_validation_error_result(
    name: str, arguments: dict[str, Any], is_async: bool
) -> None:
    detail = _assert_invalid_arguments(await _call(_server(), name, arguments, is_async=is_async))
    assert "rogue" in detail["non_field_errors"][0]


# ---------- an input serializer's own rejection ----------


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("service", {"amount_cents": 1}, "number"),
        ("selector", {"known": "not a number"}, "known"),
        ("chain", {"number": "A", "amount_cents": -1}, "amount_cents"),
    ],
)
async def test_an_input_serializer_rejection_is_a_validation_error_result(
    name: str, arguments: dict[str, Any], field: str, is_async: bool
) -> None:
    detail = _assert_invalid_arguments(await _call(_server(), name, arguments, is_async=is_async))
    # Keyed by field, exactly as ``data.detail`` was.
    assert list(detail) == [field]


# ---------- a value the spec's filter refuses ----------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_a_refused_filter_value_is_a_validation_error_result(is_async: bool) -> None:
    """Raised out of drf-services' queryset shaping, inside ``dispatch_spec``.

    It escaped every arm before, so the endpoint answered HTTP 500 with JSON-RPC
    ``-32603`` (see the wire test below). "Value out of range" is the spec's own
    example of input validation.
    """
    detail = _assert_invalid_arguments(
        await _call(_server(), "filtered", {"ordering": "--amount"}, is_async=is_async)
    )
    assert "not one of the available choices" in detail["ordering"][0]


# ---------- the in-process entry points ----------


def test_call_tool_answers_an_unexpected_argument_with_a_result() -> None:
    """``call_tool`` raised DRF's ``ValidationError`` out to the caller."""
    result = _server().call_tool(
        "service", {"number": "A", "amount_cents": 1, "rogue": 1}, user=None
    )
    detail = _assert_invalid_arguments(result.to_dict())
    assert "rogue" in detail["non_field_errors"][0]


def test_call_tool_answers_an_input_serializer_rejection_with_a_result() -> None:
    result = _server().call_tool("service", {"amount_cents": 1}, user=None)
    assert list(_assert_invalid_arguments(result.to_dict())) == ["number"]


@pytest.mark.django_db
def test_call_tool_answers_a_refused_filter_value_with_a_result() -> None:
    result = _server().call_tool("filtered", {"ordering": "--amount"}, user=None)
    assert "ordering" in _assert_invalid_arguments(result.to_dict())


def test_call_tool_echoes_the_arguments_only_when_configured(settings) -> None:
    """``INCLUDE_VALIDATION_VALUE`` governs this result as it governed ``data.value``."""
    arguments = {"amount_cents": 1}
    error = tool_error(_server().call_tool("service", arguments, user=None).to_dict())
    assert "value" not in error
    settings.REST_FRAMEWORK_MCP = {
        **getattr(settings, "REST_FRAMEWORK_MCP", {}),
        "INCLUDE_VALIDATION_VALUE": True,
    }
    error = tool_error(_server().call_tool("service", arguments, user=None).to_dict())
    assert error["value"] == arguments


async def test_acall_tool_answers_an_unexpected_argument_with_a_result() -> None:
    result = await _server().acall_tool(
        "service", {"number": "A", "amount_cents": 1, "rogue": 1}, user=None
    )
    assert "rogue" in _assert_invalid_arguments(result)["non_field_errors"][0]


# ---------- what stays a protocol error ----------


@pytest.mark.parametrize("is_async", [False, True])
async def test_an_unknown_tool_is_still_minus_32602(is_async: bool) -> None:
    out = await _call(_server(), "nope", {}, is_async=is_async)
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("arguments", [["number"], "number", 0, False])
async def test_non_object_arguments_are_still_minus_32602(arguments: Any, is_async: bool) -> None:
    """A request failing ``CallToolRequest``'s own schema is malformed, not invalid input."""
    out = await _call(_server(), "service", arguments, is_async=is_async)
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert out.message == "'arguments' must be an object"


# ---------- the wire, in both protocol eras ----------


def _post_modern(client: Client, params: dict[str, Any]) -> Any:
    """A 2026-07-28 request: no session, the version and method carried per request."""
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {**params, "_meta": meta},
            }
        ),
        content_type="application/json",
        headers={
            "Mcp-Protocol-Version": MODERN,
            "Mcp-Method": "tools/call",
            "Mcp-Name": params["name"],
        },
    )


def _post_tools_call(client: Client, session: str | None, params: dict[str, Any]) -> Any:
    if session is None:
        return _post_modern(client, params)
    return post_jsonrpc(client, method="tools/call", params=params, session_id=session)


@pytest.fixture(params=["2025-11-25", MODERN])
def era_session(request: Any, client: Client) -> str | None:
    """A session id for the session era, ``None`` for the sessionless one."""
    if request.param == MODERN:
        return None
    return request.getfixturevalue("initialized_session")


@pytest.mark.django_db
def test_the_wire_serves_an_unexpected_argument_as_a_result(
    client: Client, era_session: str | None
) -> None:
    response = _post_tools_call(
        client,
        era_session,
        {"name": "invoices.create", "arguments": {"number": "A", "amount_cents": 1, "rogue": 1}},
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert "error" not in body, body
    detail = _assert_invalid_arguments(body["result"])
    assert "rogue" in detail["non_field_errors"][0]


@pytest.fixture
def filtered_urls(settings: Any) -> None:
    """Mount this file's server, whose ``filtered`` tool carries a ``FilterSet``.

    Listed before ``era_session`` in a test's arguments, so the session era's
    ``initialize`` reaches this mount too.
    """
    settings.ROOT_URLCONF = urlconf_for(_server())


@pytest.mark.django_db
def test_the_wire_serves_a_refused_filter_value_as_a_result(
    filtered_urls: None, client: Client, era_session: str | None
) -> None:
    """The case that was HTTP 500 with ``-32603`` in both eras, not ``-32602``."""
    response = _post_tools_call(
        client, era_session, {"name": "filtered", "arguments": {"ordering": "--amount"}}
    )
    assert response.status_code == 200, response.content
    assert "ordering" in _assert_invalid_arguments(response.json()["result"])


@pytest.mark.django_db
def test_the_wire_serves_a_serializer_rejection_as_a_result(
    client: Client, era_session: str | None
) -> None:
    response = _post_tools_call(
        client, era_session, {"name": "invoices.create", "arguments": {"amount_cents": 5}}
    )
    assert response.status_code == 200, response.content
    assert "number" in _assert_invalid_arguments(response.json()["result"])


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"name": "does.not.exist", "arguments": {}}, "Unknown tool: 'does.not.exist'"),
        ({"name": "invoices.create", "arguments": ["A"]}, "'arguments' must be an object"),
    ],
)
def test_the_wire_keeps_the_protocol_errors(
    client: Client, era_session: str | None, params: dict[str, Any], message: str
) -> None:
    response = _post_tools_call(client, era_session, params)
    body = response.json()
    assert "result" not in body, body
    assert body["error"]["code"] == -32602
    assert body["error"]["message"] == message
