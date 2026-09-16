"""``tools/list`` advertises the ``affordances`` object a tool's results carry.

drf-services renders declared ``affordances`` into every item as an
``affordances`` key that no serializer declares, so a schema derived from the
serializer alone never names it. Nothing failed: the schema does not forbid extra
keys, so every result still conformed, and a client reading the schema could not
learn the key or the codes it switches on.

Each test reads the listing through ``MCPServer.list_tools``, the public surface,
so it runs unchanged against a tree without the fix.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.testing import assert_tool_result_conforms
from tests.testapp.affordances import (
    ARCHIVE_ORDER,
    CANCEL_ORDER,
    DECLARED_CODES,
    OrderSerializer,
    fresh_server,
    order_selector_spec,
)

_ORDER_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {"number": {"type": "string"}},
    "required": ["number"],
}
"""The item schema ``OrderSerializer`` advertised before affordances existed."""


def _output_schema(server: MCPServer, name: str) -> Any:
    listing: Any = server.list_tools(user=None)
    return next(tool for tool in listing["tools"] if tool["name"] == name).get("outputSchema")


def _assert_advertises_cancel(item: Any) -> None:
    assert "affordances" in item["required"]
    assert item["properties"]["number"] == {"type": "string"}
    cancel = item["properties"]["affordances"]["properties"]["cancel"]
    assert cancel["required"] == ["available"]
    # Enumerated from the declaration, not from any one answer, so a client can
    # switch on the code exhaustively.
    assert cancel["properties"]["code"]["enum"] == DECLARED_CODES


def _order_service(**overrides: Any) -> ServiceSpec[Any, Any, Any]:
    fields: dict[str, Any] = {
        "service": lambda **_: {"number": "A-1"},
        "atomic": False,
        "output_selector_spec": order_selector_spec(
            SelectorKind.RETRIEVE, selector=lambda result, **_: result
        ),
        **overrides,
    }
    return ServiceSpec(**fields)


def test_a_retrieve_selector_tool_advertises_its_affordances() -> None:
    server = fresh_server()
    server.register_selector_tool(
        name="get_order", spec=order_selector_spec(SelectorKind.RETRIEVE), permissions=[]
    )

    _assert_advertises_cancel(_output_schema(server, "get_order"))


def test_an_unpaginated_list_tool_advertises_them_on_each_array_item() -> None:
    server = fresh_server()
    server.register_selector_tool(
        name="list_orders", spec=order_selector_spec(SelectorKind.LIST), permissions=[]
    )
    schema = _output_schema(server, "list_orders")

    assert schema["type"] == "array"
    _assert_advertises_cancel(schema["items"])


def test_a_paginated_list_tool_advertises_them_on_each_envelope_item() -> None:
    """On the items, and not on the envelope, which is this transport's own shape."""
    server = fresh_server()
    server.register_selector_tool(
        name="page_orders",
        spec=order_selector_spec(SelectorKind.LIST),
        paginate=True,
        permissions=[],
    )
    schema = _output_schema(server, "page_orders")

    assert set(schema["properties"]) == {"items", "page", "totalPages", "hasNext"}
    assert schema["required"] == ["items", "page", "totalPages", "hasNext"]
    _assert_advertises_cancel(schema["properties"]["items"]["items"])


def test_a_service_tool_advertises_its_output_selector_spec_s_affordances() -> None:
    server = fresh_server()
    server.register_service_tool(name="place_order", spec=_order_service(), permissions=[])

    _assert_advertises_cancel(_output_schema(server, "place_order"))


def test_a_service_s_own_affordances_are_not_advertised() -> None:
    """A service's own ``affordances`` are what a call is refused against, and are
    never rendered, so they must not reach the schema of what it returns."""
    server = fresh_server()
    server.register_service_tool(
        name="cancel_order",
        spec=_order_service(
            affordances=CANCEL_ORDER.affordances,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda result, **_: result,
                output_serializer=OrderSerializer,
            ),
        ),
        permissions=[],
    )

    assert _output_schema(server, "cancel_order") == _ORDER_ITEM


_ARCHIVABLE: dict[str, Any] = {"affordances": {"archive": ARCHIVE_ORDER}}
"""A declaration a chain can render. One asking a condition is refused at chain
registration (see ``test_chain_tool_binding_affordances``), so this is the only
shape through which a chain's schema still reaches the ``affordances`` object."""


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(order_selector_spec(SelectorKind.RETRIEVE, **_ARCHIVABLE), id="selector-step"),
        pytest.param(
            _order_service(
                output_selector_spec=order_selector_spec(
                    SelectorKind.RETRIEVE, selector=lambda result, **_: result, **_ARCHIVABLE
                )
            ),
            id="service-step",
        ),
    ],
)
async def test_a_chain_tool_advertises_its_output_step_s_affordances(spec: Any) -> None:
    """Advertised, and served in the advertised shape.

    The call is what keeps this honest: a chain once advertised the object for
    declarations that then failed every call, so a schema assertion alone would
    agree with that bug."""
    server = fresh_server()
    server.register_chain_tool(
        name="chain", steps=[ChainStep("out", spec)], atomic=False, permissions=[]
    )

    schema = _output_schema(server, "chain")
    archive = schema["properties"]["affordances"]["properties"]["archive"]
    # No conditions, so no code to enumerate and no reason to give.
    assert archive == {
        "type": "object",
        "properties": {"available": {"type": "boolean"}},
        "required": ["available"],
    }
    tool: Any = next(t for t in server.list_tools(user=None)["tools"] if t["name"] == "chain")
    result: Any = await server.acall_tool("chain", user=None)
    assert result["structuredContent"]["affordances"] == {"archive": {"available": True}}
    assert_tool_result_conforms(tool, result)


def test_a_chain_under_output_all_still_advertises_no_schema() -> None:
    """``{alias: rendered}`` has no single schema, affordances or not."""
    server = fresh_server()
    server.register_chain_tool(
        name="chain",
        steps=[ChainStep("out", order_selector_spec(SelectorKind.RETRIEVE, **_ARCHIVABLE))],
        output_all=True,
        permissions=[],
    )

    assert _output_schema(server, "chain") is None


@pytest.mark.parametrize(
    ("kind", "paginate", "expected"),
    [
        pytest.param(SelectorKind.RETRIEVE, False, _ORDER_ITEM, id="retrieve"),
        pytest.param(SelectorKind.LIST, False, {"type": "array", "items": _ORDER_ITEM}, id="list"),
        pytest.param(
            SelectorKind.LIST,
            True,
            {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": _ORDER_ITEM},
                    "page": {"type": "integer"},
                    "totalPages": {"type": "integer"},
                    "hasNext": {"type": "boolean"},
                },
                "required": ["items", "page", "totalPages", "hasNext"],
            },
            id="paginated",
        ),
    ],
)
def test_a_spec_without_affordances_advertises_exactly_what_it_did_before(
    kind: SelectorKind, paginate: bool, expected: dict[str, Any]
) -> None:
    server = fresh_server()
    server.register_selector_tool(
        name="orders",
        spec=order_selector_spec(kind, affordances=None),
        paginate=paginate,
        permissions=[],
    )
    server.register_service_tool(
        name="place_order",
        spec=_order_service(
            output_selector_spec=order_selector_spec(
                SelectorKind.RETRIEVE, selector=lambda result, **_: result, affordances=None
            )
        ),
        permissions=[],
    )

    assert _output_schema(server, "orders") == expected
    assert _output_schema(server, "place_order") == _ORDER_ITEM
