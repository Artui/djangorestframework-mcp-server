"""A tool whose result renders as a list advertises, and serves, a list.

``build_output_schema`` has always been kind-aware, but ``tools/list`` only told it
the kind for a selector tool. A service tool whose ``output_selector_spec``
re-fetches a ``LIST``, and a chain whose output step renders one, advertised the
bare item schema while ``structuredContent`` carried an array -- a result every
strict client rejects. And a chain rendered a *service* step as one object
whatever its re-fetch returned, so that shape did not get as far as serving
anything: the serializer was handed the whole set as a single row.

Every case registers with ``include_output_schema=True``, calls the tool, and runs
the served payload through ``assert_tool_result_conforms``, which is what a client
holding the schema would check. The ``RETRIEVE`` twin of each rides along, so a fix
that answered every service or chain with an array would fail too; the selector
tool is the precedent the others now follow, and holds that its own shape did not
move.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.testing import assert_tool_result_conforms
from tests.testapp.affordances import fresh_server, order_selector_spec

_ROW: dict[str, Any] = {"number": "A-1"}


def _orders(kind: SelectorKind) -> Any:
    # No affordances: the declaration is not what this file is about, and a chain
    # refuses a rendered step whose affordances ask a condition.
    return order_selector_spec(kind, affordances=None)


def _re_fetching_service(kind: SelectorKind) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=lambda **_: None, atomic=False, output_selector_spec=_orders(kind))


def _register(server: MCPServer, shape: str, kind: SelectorKind) -> None:
    common: dict[str, Any] = {"name": "orders", "permissions": [], "include_output_schema": True}
    if shape == "selector_tool":
        server.register_selector_tool(spec=_orders(kind), **common)
    elif shape == "service_tool":
        server.register_service_tool(spec=_re_fetching_service(kind), **common)
    elif shape == "chain_selector_step":
        server.register_chain_tool(steps=[ChainStep("out", _orders(kind))], atomic=False, **common)
    else:
        server.register_chain_tool(
            steps=[ChainStep("out", _re_fetching_service(kind))], atomic=False, **common
        )


_SHAPES = ["selector_tool", "service_tool", "chain_selector_step", "chain_service_step"]


@pytest.mark.parametrize("shape", _SHAPES)
@pytest.mark.parametrize(
    ("kind", "served"),
    [
        pytest.param(SelectorKind.LIST, [_ROW], id="list"),
        pytest.param(SelectorKind.RETRIEVE, _ROW, id="retrieve"),
    ],
)
async def test_the_served_payload_conforms_to_the_advertised_schema(
    shape: str, kind: SelectorKind, served: Any
) -> None:
    server = fresh_server()
    _register(server, shape, kind)

    listing: Any = server.list_tools(user=None)
    tool: Any = next(entry for entry in listing["tools"] if entry["name"] == "orders")
    result: Any = await server.acall_tool("orders", user=None)

    assert result.get("isError") is not True, result
    assert result["structuredContent"] == served
    assert_tool_result_conforms(tool, result)
    # Named outright as well: a schema loose enough to accept both shapes would
    # conform either way, and the shape is the contract.
    assert tool["outputSchema"]["type"] == ("array" if kind is SelectorKind.LIST else "object")


async def test_output_all_renders_a_service_step_s_list_as_a_list() -> None:
    """``output_all`` renders every step through the same function, so it had the
    same single-object mistake. It advertises no schema, so the payload is the
    thing to assert."""
    server = fresh_server()
    server.register_chain_tool(
        name="orders",
        steps=[
            ChainStep("listed", _re_fetching_service(SelectorKind.LIST)),
            ChainStep("one", _re_fetching_service(SelectorKind.RETRIEVE)),
        ],
        atomic=False,
        output_all=True,
        permissions=[],
    )

    result: Any = await server.acall_tool("orders", user=None)

    assert result["structuredContent"] == {"listed": [_ROW], "one": _ROW}


@pytest.mark.parametrize("shape", ["service_tool", "chain_service_step"])
async def test_a_list_output_spec_with_no_selector_renders_and_advertises_one_object(
    shape: str,
) -> None:
    """The nested ``kind`` alone does not make a list: with no selector there is no
    re-fetch, and drf-services renders the service's own return value as one object.
    Holds the selector conjunct in ``rendered_kind``, which a kind-only check would
    drop without any other test noticing."""
    spec = ServiceSpec(
        service=lambda **_: dict(_ROW),
        atomic=False,
        output_selector_spec=order_selector_spec(
            SelectorKind.LIST, selector=None, affordances=None
        ),
    )
    server = fresh_server()
    common: dict[str, Any] = {"name": "orders", "permissions": [], "include_output_schema": True}
    if shape == "service_tool":
        server.register_service_tool(spec=spec, **common)
    else:
        server.register_chain_tool(steps=[ChainStep("out", spec)], atomic=False, **common)

    listing: Any = server.list_tools(user=None)
    tool: Any = next(entry for entry in listing["tools"] if entry["name"] == "orders")
    result: Any = await server.acall_tool("orders", user=None)

    assert result["structuredContent"] == _ROW
    assert tool["outputSchema"]["type"] == "object"
    assert_tool_result_conforms(tool, result)


class _Order(serializers.Serializer):
    number = serializers.CharField()


class _Orders(serializers.Serializer):
    items = _Order(many=True)


def _placed(*, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(item) for item in data]


@pytest.mark.parametrize("shape", ["service_tool", "chain_service_step"])
async def test_a_list_payload_service_renders_and_advertises_an_array(shape: str) -> None:
    """drf-services dispatches a ``many=True`` spec to a list result and renders it
    ``many=True`` whatever its output declaration says, so its result is an array
    with no ``LIST`` re-fetch. The schema said one object, and a chain handed the
    whole list to the serializer as a single row and failed. The ``RETRIEVE`` here
    is what a bulk spec reusing its single-item output declaration carries."""
    spec = ServiceSpec(
        service=_placed,
        atomic=False,
        many=True,
        input_serializer=_Order,
        output_selector_spec=order_selector_spec(
            SelectorKind.RETRIEVE, selector=None, affordances=None
        ),
    )
    server = fresh_server()
    common: dict[str, Any] = {"name": "orders", "permissions": [], "include_output_schema": True}
    if shape == "service_tool":
        server.register_service_tool(spec=spec, **common)
    else:
        server.register_chain_tool(
            steps=[ChainStep("out", spec, inputs=lambda ctx: {"data": ctx.args["items"]})],
            input_serializer=_Orders,
            atomic=False,
            **common,
        )

    listing: Any = server.list_tools(user=None)
    tool: Any = next(entry for entry in listing["tools"] if entry["name"] == "orders")
    result: Any = await server.acall_tool("orders", {"items": [_ROW, _ROW]}, user=None)

    assert result.get("isError") is not True, result
    assert result["structuredContent"] == [_ROW, _ROW]
    assert tool["outputSchema"]["type"] == "array"
    assert_tool_result_conforms(tool, result)


_RE_FETCHED: dict[str, Any] = {"number": "Z-9"}


@pytest.mark.parametrize("shape", ["service_tool", "chain_service_step"])
@pytest.mark.parametrize(
    ("kind", "re_fetched"),
    [
        pytest.param(SelectorKind.LIST, [_RE_FETCHED], id="list"),
        pytest.param(SelectorKind.RETRIEVE, _RE_FETCHED, id="retrieve"),
    ],
)
async def test_a_list_payload_service_is_never_re_fetched(
    shape: str, kind: SelectorKind, re_fetched: Any
) -> None:
    """drf-services never runs a ``many=True`` spec's output selector, so the list
    the service returned is what renders. A chain step ran it, handing it the whole
    list as ``instance``; under ``RETRIEVE`` the re-fetch then collapsed to one row,
    which the array this step advertises cannot hold."""
    spec = ServiceSpec(
        service=_placed,
        atomic=False,
        many=True,
        input_serializer=_Order,
        output_selector_spec=order_selector_spec(
            kind, selector=lambda **_: re_fetched, affordances=None
        ),
    )
    server = fresh_server()
    common: dict[str, Any] = {"name": "orders", "permissions": [], "include_output_schema": True}
    if shape == "service_tool":
        server.register_service_tool(spec=spec, **common)
    else:
        server.register_chain_tool(
            steps=[ChainStep("out", spec, inputs=lambda ctx: {"data": ctx.args["items"]})],
            input_serializer=_Orders,
            atomic=False,
            **common,
        )

    listing: Any = server.list_tools(user=None)
    tool: Any = next(entry for entry in listing["tools"] if entry["name"] == "orders")
    result: Any = await server.acall_tool("orders", {"items": [_ROW, _ROW]}, user=None)

    assert result.get("isError") is not True, result
    assert result["structuredContent"] == [_ROW, _ROW]
    assert_tool_result_conforms(tool, result)
