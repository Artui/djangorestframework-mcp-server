"""A dataclass-shaped ``input_serializer`` lays its validated values back too.

A bare ``@dataclass`` and a ``DataclassSerializer`` validate into a dataclass
instance rather than the ``dict`` a plain ``Serializer`` gives, and only the
``dict`` was laid over the selector's arguments. So such an input coerced and
defaulted for nothing: the selector read the caller's raw strings, and a name
only the dataclass defaulted was missing.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext


@dataclasses.dataclass
class _Counted:
    count: int
    status: str = "open"


class _CountedIn(DataclassSerializer):
    class Meta:
        dataclass = _Counted


# Defaults of its own, unlike the dataclass's, so what arrives says which won:
# registration counts no field a ``DataclassSerializer`` generates as a source,
# so a required parameter would refuse that shape before any call.
def _counted(*, count: int = 0, status: str = "closed") -> dict[str, Any]:
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["handler", "async_handler", "acall_tool"])
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
    # ``call_tool`` runs no ``input_serializer``, so it has nothing to lay back.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="counted",
        description="Count.",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_counted),
        input_serializer=input_serializer,
    )
    params: dict[str, Any] = {"name": "counted", "arguments": {"count": "3"}}

    if route == "acall_tool":
        out: Any = await server.acall_tool("counted", {"count": "3"}, user=None)
    elif route == "async_handler":
        out = await handle_tools_call_async(params, _ctx(server))
    else:
        out = await sync_to_async(handle_tools_call)(params, _ctx(server))

    assert isinstance(out, dict), f"answered {out!r}"
    assert out.get("isError") is not True, out
    # Coerced to the dataclass's type, and the default the caller left to it.
    assert out["structuredContent"] == {"count": 3, "status": "open"}
