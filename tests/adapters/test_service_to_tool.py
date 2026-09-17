"""``service_spec_to_tool`` -- what a service spec needs before it becomes a tool.

A ``many=True`` spec validates its input as a JSON array, and MCP ``arguments``
is always a JSON object. Such a tool used to register, advertise the single
item's object schema, and fail every call: drf-services refused the binding's
``BUNDLE`` default with a ``ValueError`` before validation ran. It is refused at
registration now, and the message points at the shape that works.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.serializers import InvoiceInputSerializer


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _count(*, data: Any) -> dict[str, int]:
    return {"count": len(data)}


def _bulk_spec() -> ServiceSpec:
    return ServiceSpec(
        service=_count, atomic=False, many=True, input_serializer=InvoiceInputSerializer
    )


def test_a_many_spec_is_refused_at_registration() -> None:
    server = _server()

    with pytest.raises(ImproperlyConfigured, match=r"'bulk'.*many=True.*JSON object"):
        server.register_service_tool(name="bulk", spec=_bulk_spec())

    assert "bulk" not in server.tools


def test_a_many_spec_in_a_registry_is_refused_naming_how_to_leave_it_out() -> None:
    """The path a project exposing its REST specs wholesale takes."""
    registry = SpecRegistry()
    registry.register("bulk", _bulk_spec(), tags=("write",))

    with pytest.raises(ImproperlyConfigured, match="by_tag"):
        _server().register_specs(registry)


class _Items(serializers.Serializer):
    items = InvoiceInputSerializer(many=True)


def _count_items(*, data: dict[str, Any]) -> dict[str, int]:
    return {"count": len(data["items"])}


@pytest.mark.django_db
def test_the_list_as_a_named_field_the_refusal_suggests_works() -> None:
    """Holds the refusal's advice honest: advertised as an array, and every item
    validated against the item serializer."""
    server = _server()
    server.register_service_tool(
        name="bulk",
        spec=ServiceSpec(service=_count_items, atomic=False, input_serializer=_Items),
    )
    listed: Any = handle_tools_list({}, _ctx(server))
    schema = next(t for t in listed["tools"] if t["name"] == "bulk")["inputSchema"]
    item = {"number": "A-1", "amount_cents": 1}

    ok: Any = handle_tools_call(
        {"name": "bulk", "arguments": {"items": [item, item]}}, _ctx(server)
    )
    bad: Any = handle_tools_call(
        {"name": "bulk", "arguments": {"items": [item, {**item, "amount_cents": -1}]}},
        _ctx(server),
    )

    assert schema["properties"]["items"]["type"] == "array"
    assert ok["structuredContent"] == {"count": 2}
    # Read as encoded, which is what reaches the client. DRF 3.18 keys a nested
    # list's errors by the invalid items' indexes, which encoding turns into string
    # keys; below it they are a list holding an empty object for each valid item.
    # The floor is below 3.18, so both shapes reach clients.
    items = json.loads(json.dumps(bad.to_dict()))["data"]["detail"]["items"]
    by_index = items if isinstance(items, dict) else {str(i): e for i, e in enumerate(items) if e}
    assert by_index == {"1": {"amount_cents": ["Ensure this value is greater than or equal to 0."]}}
