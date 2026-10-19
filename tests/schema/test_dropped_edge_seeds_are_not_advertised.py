"""A parameter named ``serializer`` is not offered to the model, because the call drops it.

drf-services' DRF edge drops a caller's ``request`` and ``serializer`` keys
(``DROPPED_EDGE_SEEDS``): the edge seeds the first and the validator binds the
second, and since drf-services 0.58.0 neither is in the kernel's
``RESERVED_POOL_SEEDS``, which its reflection subtracts itself. A selector or
spread service declaring ``serializer`` with a default was therefore listed in
``tools/list`` with a property the call then discarded. ``request`` was never
listed, because the reflection counts the edge's own seeds; only ``serializer``
reached the schema.

Read through ``MCPServer.list_tools``, so each test reads what a client is served.
"""

from __future__ import annotations

from typing import Any

from django_service_specs import SelectorKind
from rest_framework.permissions import AllowAny
from rest_framework_services import ArgumentBinding, SelectorSpec, ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.models import Invoice


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _schema(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    (tool,) = listed["tools"]
    return tool["inputSchema"]


def _by_pk(*, pk: int, serializer: Any = None) -> Any:
    return Invoice.objects.filter(pk=pk)


def _close(*, number: str, serializer: Any = None) -> None:
    return None


def test_a_selectors_serializer_parameter_is_not_advertised() -> None:
    server = _server()
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_by_pk, permission_classes=[AllowAny]
        ),
    )

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]


def test_a_spread_services_serializer_parameter_is_not_advertised() -> None:
    server = _server()
    server.register_service_tool(
        name="close",
        spec=ServiceSpec(service=_close, atomic=False, permission_classes=[AllowAny]),
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )

    schema = _schema(server)

    # Closed under ``REJECT``: the listing is the whole set a call may send,
    # and ``serializer`` is not in what the call reads.
    assert set(schema["properties"]) == {"number"}
    assert schema["required"] == ["number"]
    assert schema["additionalProperties"] is False


def _echo(*, note: str, serializer: Any = None, request: Any = None) -> list[dict[str, Any]]:
    return [{"note": note, "serializer": serializer, "request_is_caller_value": request == "spoof"}]


def test_the_call_drops_what_the_listing_leaves_out() -> None:
    # Why neither is offered: a caller's value for either name never reaches the
    # callable. ``serializer`` keeps its default; ``request`` is the call's own.
    server = _server()
    server.register_selector_tool(
        name="echo",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_echo, permission_classes=[AllowAny]),
    )

    result = server.call_tool(
        "echo", {"note": "hi", "serializer": "spoof", "request": "spoof"}, user=None
    ).to_dict()

    assert result["structuredContent"] == [
        {"note": "hi", "serializer": None, "request_is_caller_value": False}
    ]
