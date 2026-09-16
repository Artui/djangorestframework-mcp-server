"""Affordances, on the wire: the schema a tool advertises names the key it returns.

``assert_tool_result_conforms`` cannot see this gap, which is the reason for the
test. It validates a result against the schema, and a schema that does not forbid
extra keys validates a key it never mentions -- so a result carrying an
``affordances`` object its tool never advertised conforms. Here the question is
the stronger one a client or a model asks of a schema: is every key I receive one
it describes?
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.testing import assert_tool_result_conforms
from tests.testapp.affordances import fresh_server, order_selector_spec


def _server() -> MCPServer:
    server = fresh_server()
    server.register_selector_tool(
        name="get_order", spec=order_selector_spec(SelectorKind.RETRIEVE), permissions=[]
    )
    server.register_selector_tool(
        name="list_orders", spec=order_selector_spec(SelectorKind.LIST), permissions=[]
    )
    server.register_selector_tool(
        name="page_orders",
        spec=order_selector_spec(SelectorKind.LIST),
        paginate=True,
        permissions=[],
    )
    server.register_service_tool(
        name="place_order",
        spec=ServiceSpec(
            service=lambda **_: {"number": "A-1"},
            atomic=False,
            output_selector_spec=order_selector_spec(
                SelectorKind.RETRIEVE, selector=lambda result, **_: result
            ),
        ),
        permissions=[],
    )
    return server


def _undescribed(schema: Any, value: Any, path: str = "$") -> list[str]:
    """Every key in ``value`` that ``schema`` does not name, and every enum it breaks."""
    if isinstance(value, dict):
        properties: dict[str, Any] = schema.get("properties", {})
        found: list[str] = []
        for key, child in value.items():
            if key in properties:
                found.extend(_undescribed(properties[key], child, f"{path}.{key}"))
            else:
                found.append(f"{path}.{key}")
        return found
    if isinstance(value, list):
        return [
            problem
            for index, item in enumerate(value)
            for problem in _undescribed(schema["items"], item, f"{path}[{index}]")
        ]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}={value!r}"]
    return []


def _answers(structured: Any) -> list[Any]:
    """The ``affordances`` object on each rendered item, wherever the item sits."""
    if isinstance(structured, list):
        items = structured
    elif "items" in structured:
        items = structured["items"]
    else:
        items = [structured]
    return [item["affordances"] for item in items]


@pytest.mark.parametrize("name", ["get_order", "list_orders", "page_orders", "place_order"])
async def test_the_advertised_schema_describes_the_affordances_a_call_returns(name: str) -> None:
    server = _server()
    listing: Any = await server.alist_tools(user=None)
    tool = next(entry for entry in listing["tools"] if entry["name"] == name)

    result: Any = await server.acall_tool(name, {}, user=None)
    structured = result["structuredContent"]

    # The key is really there, refused on the second condition...
    assert _answers(structured) == [
        {"cancel": {"available": False, "code": "books_closed", "reason": "The books are closed."}}
    ]
    # ...the conformance helper passes, as it did while the schema was silent...
    assert_tool_result_conforms(tool, result)
    # ...and only this tells a described key from a tolerated one.
    assert _undescribed(tool["outputSchema"], structured) == []
