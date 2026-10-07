"""``page`` and ``limit`` reach a selector alike on every route.

Both belong to the read pipeline's pagination, so the wire and ``acall_tool``
strip them from a selector's arguments, while ``call_tool`` passed them through:
a ``**kwargs`` selector received them on one route only.
"""

from __future__ import annotations

from typing import Any

import django_filters
import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from tests.testapp.models import Invoice

_ROUTES = ["call_tool", "acall_tool", "handler", "async_handler"]


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


async def _structured(server: MCPServer, route: str, arguments: dict[str, Any]) -> Any:
    """What calling ``read`` through ``route`` returns as structured content."""
    if route == "call_tool":
        result: Any = await sync_to_async(server.call_tool)("read", arguments, user=None)
        out: Any = result.to_dict()
    elif route == "acall_tool":
        out = await server.acall_tool("read", arguments, user=None)
    else:
        params: dict[str, Any] = {"name": "read", "arguments": arguments}
        handler: Any = (
            handle_tools_call_async
            if route == "async_handler"
            else sync_to_async(handle_tools_call)
        )
        out = await handler(params, _ctx(server))
    assert isinstance(out, dict), f"answered {out!r}"
    assert out.get("isError") is not True, out
    return out["structuredContent"]


def _register(server: MCPServer, selector: Any, **kwargs: Any) -> MCPServer:
    server.register_selector_tool(
        name="read",
        description="Read.",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
        **kwargs,
    )
    return server


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _anything(**kwargs: Any) -> dict[str, Any]:
    # The pool's seeds arrive too; only what the caller sent is compared.
    return {name: kwargs[name] for name in ("page", "limit", "status") if name in kwargs}


def _page(*, page: int = 1) -> dict[str, Any]:
    return {"page": page}


class _PageIn(drf_serializers.Serializer):
    page = drf_serializers.IntegerField(required=False)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_every_route_hands_a_selector_the_same_arguments(route: str) -> None:
    server = _register(_server(), _anything)

    out = await _structured(server, route, {"page": 2, "limit": 5, "status": "open"})

    assert out == {"status": "open"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_name_the_input_serializer_lays_back_reaches_the_selector_on_every_route(
    route: str,
) -> None:
    # The wire strips ``page`` and the ``input_serializer`` lays it back;
    # ``call_tool`` runs no ``input_serializer``, so it keeps the name instead,
    # or the selector ran on its own default.
    server = _register(_server(), _page, input_serializer=_PageIn)

    out = await _structured(server, route, {"page": 3})

    assert out == {"page": 3}


class _AmountCeiling(django_filters.FilterSet):
    """A ``FilterSet`` declaring ``limit`` as a filter of its own."""

    limit = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="lte")

    class Meta:
        model = Invoice
        fields: list[str] = []


class _Number(drf_serializers.Serializer):
    number = drf_serializers.CharField()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_filter_set_still_reads_a_pagination_named_filter_on_every_route(
    route: str,
) -> None:
    await Invoice.objects.acreate(number="A-1", amount_cents=50)
    await Invoice.objects.acreate(number="A-2", amount_cents=500)
    server = _server()
    server.register_selector_tool(
        name="read",
        description="Read.",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Invoice.objects.order_by("pk"),
            output_serializer=_Number,
            filter_set=_AmountCeiling,
        ),
    )

    out = await _structured(server, route, {"limit": 100})

    assert out == [{"number": "A-1"}]


class _PageAndLimit(drf_serializers.Serializer):
    page = drf_serializers.IntegerField()
    limit = drf_serializers.IntegerField()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_service_tools_page_and_limit_are_its_own_on_every_route(route: str) -> None:
    # No read pipeline paginates a service tool, so nothing strips either name.
    server = _server()
    server.register_service_tool(
        name="read",
        description="Read.",
        spec=ServiceSpec(
            service=lambda *, data: dict(data), input_serializer=_PageAndLimit, atomic=False
        ),
    )

    out = await _structured(server, route, {"page": 2, "limit": 5})

    assert out == {"page": 2, "limit": 5}
