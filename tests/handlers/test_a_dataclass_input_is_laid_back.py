"""A dataclass-shaped ``input_serializer`` lays its validated values back too.

A bare ``@dataclass`` and a ``DataclassSerializer`` validate into a dataclass
instance rather than the ``dict`` a plain ``Serializer`` gives, and only the
``dict`` was laid over the selector's arguments. So such an input coerced and
defaulted for nothing: the selector read the caller's raw strings, and a name
only the dataclass defaulted was missing.

Dispatch was not the only reader to follow. Registration counted no field a
``DataclassSerializer`` generates as a source, so a selector requiring one was
refused although every call would fill it, and the schema counted no dataclass
default as filling a name, so a name a call could leave out was advertised as
required. All three now read what is laid back through one function,
``schema.utils.laid_back_inputs``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from tests.utils import tool_error

# Every route that runs a selector tool's ``input_serializer``: ``call_tool``
# runs none, so it has nothing to lay back.
_ROUTES = ["handler", "async_handler", "acall_tool"]


@dataclasses.dataclass
class _Counted:
    count: int
    status: str = "open"


class _CountedIn(DataclassSerializer):
    class Meta:
        dataclass = _Counted


# Requires both names, so registration has to count each as filled: ``count``
# by a generated field, ``status`` by the dataclass's default.
def _counted(*, count: int, status: str) -> dict[str, Any]:
    return {"count": count, "status": status}


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        conventions=server.conventions,
    )


async def _via(server: MCPServer, route: str, name: str, arguments: dict[str, Any]) -> Any:
    if route == "acall_tool":
        return await server.acall_tool(name, arguments, user=None)
    params: dict[str, Any] = {"name": name, "arguments": arguments}
    if route == "async_handler":
        return await handle_tools_call_async(params, _ctx(server))
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize(
    "input_serializer",
    [
        pytest.param(_Counted, id="bare-dataclass"),
        pytest.param(_CountedIn, id="dataclass-serializer"),
    ],
)
async def test_a_dataclass_inputs_validated_values_reach_the_selector(
    route: str, input_serializer: type
) -> None:
    # The ``DataclassSerializer``'s ``count`` is a field it generates, which
    # registration once counted as no source, so a selector requiring it was
    # refused before any call although dispatch fills it on every one.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="counted",
        description="Count.",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_counted),
        input_serializer=input_serializer,
    )

    out = await _via(server, route, "counted", {"count": "3"})

    assert isinstance(out, dict), f"answered {out!r}"
    assert out.get("isError") is not True, out
    # Coerced to the dataclass's type, and the default the caller left to it.
    assert out["structuredContent"] == {"count": 3, "status": "open"}


# ----- registration, the schema and dispatch agree -----


@dataclasses.dataclass
class _BareOrder:
    count: int
    status: str = "open"
    # Generated read-only, so it is laid back as this default whatever is sent.
    kind: str = dataclasses.field(
        default="order", metadata={"serializer_kwargs": {"read_only": True}}
    )


@dataclasses.dataclass
class _Order:
    count: int
    number: int = 0
    status: str = "open"
    priority: int = 9
    # Generated read-only, so only its default makes it a source.
    kind: str = dataclasses.field(
        default="order", metadata={"serializer_kwargs": {"read_only": True}}
    )


class _OrderIn(DataclassSerializer):
    """Declared fields over dataclass defaults, beside the generated ``count``, ``status`` and ``kind``.

    ``number`` is required although the dataclass defaults it, so the call is
    refused without it. ``priority``'s own default outranks the dataclass's.
    """

    number = serializers.IntegerField()
    priority = serializers.IntegerField(default=2)

    class Meta:
        dataclass = _Order


class _OrderSerializer(serializers.Serializer):
    """A plain ``Serializer``, whose ``dict`` leaves out a read-only field's default."""

    count = serializers.IntegerField()
    status = serializers.CharField(default="open")
    kind = serializers.CharField(read_only=True, default="order")


def _bare_order(*, count: int, status: str, kind: str) -> dict[str, Any]:
    return {"count": count, "status": status, "kind": kind}


def _order(*, count: int, number: int, status: str, priority: int, kind: str) -> dict[str, Any]:
    return {"count": count, "number": number, "status": status, "priority": priority, "kind": kind}


# What a call sends for each name it does send.
_SENT: dict[str, Any] = {"count": "3", "number": "4", "kind": "sent"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize(
    ("input_serializer", "selector", "required", "received"),
    [
        pytest.param(
            _BareOrder,
            _bare_order,
            ["count"],
            {"count": 3, "status": "open", "kind": "order"},
            id="bare-dataclass",
        ),
        pytest.param(
            _OrderIn,
            _order,
            ["count", "number"],
            {"count": 3, "number": 4, "status": "open", "priority": 2, "kind": "order"},
            id="dataclass-serializer",
        ),
        pytest.param(
            _OrderSerializer,
            _bare_order,
            # The read-only field reaches the selector only as the caller sent it.
            ["count", "kind"],
            {"count": 3, "status": "open", "kind": "sent"},
            id="plain-serializer",
        ),
    ],
)
async def test_registration_the_schema_and_dispatch_agree_on_what_an_input_lays_back(
    route: str,
    input_serializer: type,
    selector: Any,
    required: list[str],
    received: dict[str, Any],
) -> None:
    # The selector requires every name, so each one the call leaves out is
    # one the input lays back. Registration admits the tool, the schema
    # requires exactly the names no call may leave out, a call sending only
    # those is served with what the input laid back, and a call leaving out
    # any one of them is refused for it.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="order",
        description="An order.",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
        input_serializer=input_serializer,
    )
    listed: Any = server.list_tools(user=None)
    schema = next(tool for tool in listed["tools"] if tool["name"] == "order")["inputSchema"]
    sent = {name: _SENT[name] for name in required}

    out = await _via(server, route, "order", sent)

    assert sorted(schema.get("required", [])) == required
    assert out.get("isError") is not True, out
    assert out["structuredContent"] == received
    for name in required:
        refused = await _via(
            server, route, "order", {key: value for key, value in sent.items() if key != name}
        )
        assert set(tool_error(refused)["detail"]) == {name}
