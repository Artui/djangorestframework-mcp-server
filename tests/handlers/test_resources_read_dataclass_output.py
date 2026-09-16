"""``resources/read`` renders a resource whose selector declares a raw dataclass output.

Tools render through drf-services' ``render_spec_output``, which resolves a
dataclass to a serializer class before instantiating it. A resource renders in
this package instead (``build_resource_contents``), because its binding holds a
bare selector callable rather than a spec to hand upstream -- so it has to make
the same resolution itself, or a dataclass output that a tool renders fine
crashes every read of the resource.

Both handlers are driven, through a real ``register_resource``: the sync and
async read paths are parallel implementations, and a fix reaching one of them
is the drift ``build_resource_contents`` exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.types.context import MCPCallContext


@dataclass
class Invoice:
    number: str
    amount_cents: int


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(
            selector=lambda pk: Invoice(number=f"INV-{pk}", amount_cents=100),
            kind=SelectorKind.RETRIEVE,
            output_serializer=Invoice,
        ),
    )
    server.register_resource(
        name="invoices",
        uri_template="invoices://all",
        selector=SelectorSpec(
            selector=lambda: [Invoice("INV-1", 100), Invoice("INV-2", 250)],
            kind=SelectorKind.LIST,
            output_serializer=Invoice,
        ),
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


def _body(out: Any) -> Any:
    assert isinstance(out, dict), out
    return json.loads(out["contents"][0]["text"])


def test_sync_read_renders_a_dataclass_retrieve() -> None:
    server = _server()
    out = handle_resources_read({"uri": "invoices://7"}, _ctx(server))
    assert _body(out) == {"number": "INV-7", "amount_cents": 100}


def test_sync_read_renders_a_dataclass_list() -> None:
    server = _server()
    out = handle_resources_read({"uri": "invoices://all"}, _ctx(server))
    assert [row["number"] for row in _body(out)] == ["INV-1", "INV-2"]


async def test_async_read_renders_a_dataclass_retrieve() -> None:
    server = _server()
    out = await handle_resources_read_async({"uri": "invoices://7"}, _ctx(server))
    assert _body(out) == {"number": "INV-7", "amount_cents": 100}


async def test_async_read_renders_a_dataclass_list() -> None:
    server = _server()
    out = await handle_resources_read_async({"uri": "invoices://all"}, _ctx(server))
    assert [row["number"] for row in _body(out)] == ["INV-1", "INV-2"]


class _NotASerializer:
    pass


@pytest.mark.parametrize("declared", [_NotASerializer, object()])
def test_registration_refuses_an_output_no_read_could_render(declared: object) -> None:
    """The same boundary the tool adapters hold, for the same reason.

    Nothing derives a schema for a resource, so this cannot fail discovery the
    way a tool's could -- but every read would fail, and only at read time.
    """
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match=r"^resource 'bad': output serializer"):
        server.register_resource(
            name="bad",
            uri_template="bad://x",
            selector=SelectorSpec(selector=lambda: {}, kind=SelectorKind.RETRIEVE),
            output_serializer=declared,  # the explicit kwarg wins over the spec's
        )


def test_registration_checks_the_specs_output_when_no_kwarg_is_given() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match=r"^resource 'bad': output serializer"):
        server.register_resource(
            name="bad",
            uri_template="bad://x",
            selector=SelectorSpec(
                selector=lambda: {}, kind=SelectorKind.RETRIEVE, output_serializer=_NotASerializer
            ),
        )
