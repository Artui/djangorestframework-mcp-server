"""``service_spec_to_tool`` -- what a service spec needs before it becomes a tool.

A ``many=True`` spec validates its input as a list, and MCP ``arguments`` is always
a JSON object, so the list travels under the one argument ``spec.many_argument``
names. Such a spec used to be refused here; it registers now, and what is refused
instead is a declaration that would stop the list reaching dispatch or make every
call fail: a URL kwarg or query param taking the argument's name, a spreading
argument binding, and a collection target beside the list.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, QueryParam, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import ArgumentBinding
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


def _bulk_spec(**overrides: Any) -> ServiceSpec:
    return ServiceSpec(
        service=_count,
        atomic=False,
        many=True,
        input_serializer=InvoiceInputSerializer,
        **overrides,
    )


def test_a_many_spec_registers() -> None:
    server = _server()

    server.register_service_tool(name="bulk", spec=_bulk_spec())

    assert "bulk" in server.tools


def test_a_many_spec_in_a_registry_registers() -> None:
    """The path a project exposing its REST specs wholesale takes, which the refusal
    once made it narrow with ``by_tag`` first."""
    registry = SpecRegistry()
    registry.register("bulk", _bulk_spec(), tags=("write",))
    server = _server()

    server.register_specs(registry)

    assert "bulk" in server.tools


# ---------- a channel taking the list's name ----------


@pytest.mark.parametrize(
    "channel",
    [
        pytest.param({"url_kwargs": (UrlKwarg("items"),)}, id="url_kwarg"),
        pytest.param({"query_params": (QueryParam("items"),)}, id="query_param"),
    ],
)
def test_a_channel_named_as_the_list_argument_is_refused(channel: dict[str, Any]) -> None:
    """Both channels pop their name out of the arguments before dispatch, so the
    list would never arrive and every call would fail as a missing argument."""
    server = _server()

    with pytest.raises(ImproperlyConfigured, match=r"'bulk'.*'items'.*many_argument"):
        server.register_service_tool(name="bulk", spec=_bulk_spec(), **channel)

    assert "bulk" not in server.tools


def test_the_collision_is_with_the_name_the_spec_declares() -> None:
    """The refusal reads ``spec.many_argument``, not the default: a spec whose list
    travels as ``rows`` is refused a ``rows`` kwarg and may route one named
    ``items``."""
    server = _server()

    with pytest.raises(ImproperlyConfigured, match=r"'rows'.*many_argument"):
        server.register_service_tool(
            name="bulk", spec=_bulk_spec(many_argument="rows"), url_kwargs=(UrlKwarg("rows"),)
        )
    server.register_service_tool(
        name="bulk", spec=_bulk_spec(many_argument="rows"), url_kwargs=(UrlKwarg("items"),)
    )

    assert "bulk" in server.tools


def test_a_single_item_spec_may_route_a_channel_named_items() -> None:
    """Only a list payload reserves the name. Holds the ``many`` half of the
    collision check, which a name-only comparison would drop."""
    server = _server()

    server.register_service_tool(
        name="one",
        spec=ServiceSpec(service=_count, atomic=False, input_serializer=InvoiceInputSerializer),
        url_kwargs=(UrlKwarg("items"),),
    )

    assert "one" in server.tools


# ---------- declarations a list payload cannot honour ----------


@pytest.mark.parametrize(
    "binding", [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS]
)
def test_a_spreading_argument_binding_is_refused(binding: ArgumentBinding) -> None:
    """drf-services raises ``ValueError`` for it on every dispatch: the service
    receives the whole list as ``data``, so there is nothing to spread."""
    server = _server()

    with pytest.raises(ImproperlyConfigured, match=r"'bulk'.*argument_binding"):
        server.register_service_tool(name="bulk", spec=_bulk_spec(), argument_binding=binding)


def test_a_collection_target_beside_the_list_is_refused() -> None:
    """The list-payload dispatch never resolves a collection, so the selector would
    be declared and never run; drf-services' own views refuse the pair too."""
    spec = ServiceSpec(
        service=_count,
        atomic=False,
        many=True,
        collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
    )

    with pytest.raises(ImproperlyConfigured, match=r"'bulk'.*collection_selector_spec"):
        _server().register_service_tool(name="bulk", spec=spec)


# ---------- the documented alternative still works ----------


class _Items(serializers.Serializer):
    items = InvoiceInputSerializer(many=True)


def _count_items(*, data: dict[str, Any]) -> dict[str, int]:
    return {"count": len(data["items"])}


@pytest.mark.django_db
def test_the_list_as_a_named_serializer_field_works() -> None:
    """A spec that must accept arguments beside the list keeps this shape, so it
    stays documented: advertised as an array, and every item validated against the
    item serializer."""
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
