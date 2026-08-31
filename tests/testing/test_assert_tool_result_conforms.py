"""The conformance helper, and the contrast that is the reason it exists.

The headline is `test_a_type_break_that_the_key_set_check_calls_conforming`: one tool,
one call, and two assertions over the same pair -- the key-set comparison a
suite writes on its own passes it, and this helper fails it. Everything else
here covers the edges of the message that failure carries.
"""

from __future__ import annotations

import sys
from decimal import Decimal
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
from rest_framework_mcp.testing import assert_tool_result_conforms
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer, LedgerSerializer


def _server(output_serializer: type) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="list_invoices",
        description="List invoices.",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda **_: Invoice.objects.all().order_by("id"),
            output_serializer=output_serializer,
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


def _call(output_serializer: type) -> tuple[Any, Any]:
    """The advertised entry and the returned result for one registered tool."""
    server = _server(output_serializer)
    ctx = _ctx(server)
    tool: Any = handle_tools_list(None, ctx)["tools"][0]
    result: Any = handle_tools_call({"name": "list_invoices", "arguments": {}}, ctx)
    return tool, result


@pytest.mark.django_db
def test_a_conforming_tool_passes() -> None:
    """The real pipeline, end to end, with no disagreement to find."""
    Invoice.objects.create(number="FV/2026/0043", amount_cents=124000, sent=True)

    tool, result = _call(InvoiceOutputSerializer)

    assert_tool_result_conforms(tool, result)


@pytest.mark.django_db
def test_a_type_break_that_the_key_set_check_calls_conforming() -> None:
    """Same tool, same call, two assertions: one passes it and one catches it."""
    Invoice.objects.create(number="FV/2026/0043", amount_cents=124000, sent=True)

    tool, result = _call(LedgerSerializer)
    advertised = tool["outputSchema"]["items"]["properties"]
    row = result["structuredContent"][0]

    # The schema says integer; an integer is not what arrived.
    assert advertised["amount_cents"] == {"type": "integer"}
    assert row["amount_cents"] == "1240.00"

    # The check a suite writes for itself. It passes -- no key moved.
    assert set(advertised) == set(row)

    with pytest.raises(AssertionError) as caught:
        assert_tool_result_conforms(tool, result)

    message = str(caught.value)
    assert "'list_invoices'" in message
    assert "1 problem" in message
    assert "$[0].amount_cents: advertised type 'integer', got string '1240.00'" in message


def test_a_format_break_is_caught_too() -> None:
    """Types are the common break; a format is the one a type check still misses."""
    tool = {
        "name": "get_invoice",
        "outputSchema": {
            "type": "object",
            "properties": {"due": {"type": "string", "format": "date-time"}},
        },
    }
    result = {"structuredContent": {"due": "soon"}}

    with pytest.raises(AssertionError) as caught:
        assert_tool_result_conforms(tool, result)

    assert "$.due: advertised format 'date-time', got string 'soon'" in str(caught.value)


def test_every_problem_is_reported_not_only_the_first() -> None:
    """A consumer fixing one break per run is a consumer who stops running it."""
    tool = {
        "name": "list_invoices",
        "outputSchema": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"amount_cents": {"type": "integer"}},
                "required": ["number"],
            },
        },
    }
    result = {"structuredContent": [{"number": "a", "amount_cents": "12"}, {"amount_cents": 5}]}

    with pytest.raises(AssertionError) as caught:
        assert_tool_result_conforms(tool, result)

    message = str(caught.value)
    assert "2 problems" in message
    # Ordered by where in the payload they sit, and the keyword-specific
    # wording gives way to jsonschema's for anything but a type or a format.
    assert message.splitlines()[-2:] == [
        "  - $[0].amount_cents: advertised type 'integer', got string '12'",
        "  - $[1]: 'number' is a required property",
    ]


def test_an_unadvertised_schema_is_a_failure_not_a_pass() -> None:
    """Nothing to conform to means the assertion would hold for any result."""
    with pytest.raises(AssertionError) as caught:
        assert_tool_result_conforms({"name": "list_invoices"}, {"structuredContent": []})

    message = str(caught.value)
    assert "advertises no 'outputSchema'" in message
    assert "INCLUDE_OUTPUT_SCHEMA" in message


def test_an_advertised_schema_with_no_structured_content_is_a_failure() -> None:
    with pytest.raises(AssertionError) as caught:
        assert_tool_result_conforms(
            {"name": "list_invoices", "outputSchema": {"type": "array"}},
            {"content": [{"type": "text", "text": "[]"}]},
        )

    message = str(caught.value)
    assert "carries no 'structuredContent'" in message
    assert "error result" not in message


def test_an_error_result_says_so_rather_than_reading_as_a_misconfiguration() -> None:
    with pytest.raises(AssertionError) as caught:
        assert_tool_result_conforms(
            {"name": "list_invoices", "outputSchema": {"type": "array"}},
            {"isError": True, "content": [{"type": "text", "text": "boom"}]},
        )

    assert "The call returned an error result:" in str(caught.value)
    assert "boom" in str(caught.value)


def test_a_tool_with_no_name_still_produces_a_message() -> None:
    with pytest.raises(AssertionError, match="'<unnamed>'"):
        assert_tool_result_conforms({}, {"structuredContent": []})


def test_the_schema_chooses_its_own_draft_when_it_names_one() -> None:
    """``$schema`` is honoured; 2020-12 is only the fallback."""
    tool = {
        "name": "get_invoice",
        "outputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {"amount_cents": {"type": "integer"}},
        },
    }

    with pytest.raises(AssertionError, match="advertised type 'integer'"):
        assert_tool_result_conforms(tool, {"structuredContent": {"amount_cents": "12"}})


@pytest.mark.parametrize(
    ("arrived", "named"),
    [
        (None, "null"),
        (True, "boolean"),
        (12, "integer"),
        (1.5, "number"),
        ([], "array"),
        ({}, "object"),
        (Decimal("1.5"), "Decimal"),
    ],
)
def test_what_arrived_is_named_in_json_s_vocabulary(arrived: Any, named: str) -> None:
    """``True`` is a boolean here, not an integer, however Python spells it.

    A value JSON has no word for keeps its Python type name, which is usually
    the whole diagnosis -- an unserialized ``Decimal`` reached the payload.
    """
    tool = {"name": "t", "outputSchema": {"type": "string"}}

    with pytest.raises(AssertionError) as caught:
        assert_tool_result_conforms(tool, {"structuredContent": arrived})

    assert f"got {named} " in str(caught.value)


def test_a_string_that_arrived_is_recognisable_without_being_quoted_whole() -> None:
    tool = {"name": "t", "outputSchema": {"type": "integer"}}
    arrived = "x" * 500

    with pytest.raises(AssertionError) as caught:
        assert_tool_result_conforms(tool, {"structuredContent": arrived})

    message = str(caught.value)
    assert "'xxxxxxxxxx" in message
    assert "... (502 chars)" in message
    assert len(message) < 400


def test_the_missing_extra_is_named_rather_than_raised_as_a_bare_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package works without ``jsonschema``; only this helper needs it."""
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    monkeypatch.setitem(sys.modules, "jsonschema.validators", None)

    with pytest.raises(ImportError, match=r"djangorestframework-mcp-server\[test\]"):
        assert_tool_result_conforms({"name": "t", "outputSchema": {}}, {"structuredContent": {}})
