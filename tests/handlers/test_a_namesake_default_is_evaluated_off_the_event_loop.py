"""A namesake default for a URL kwarg the call left out is evaluated off the event loop.

A selector tool's ``input_serializer`` field named for a URL kwarg supplies that
kwarg's value when the call leaves it out. The async selector path called that
default while building the dispatch keywords on the event loop, so a default
that queries raised ``SynchronousOnlyOperation`` there, where the sync path
served. The keywords are now built in a worker thread, as the serializer's own
validation already was.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from tests.testapp.models import Invoice


def _default_project() -> int:
    # Any ORM read; the test database holds no invoice, so this is 7.
    return Invoice.objects.count() + 7


class _Input(serializers.Serializer):
    project_pk = serializers.IntegerField(default=_default_project)


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="tool",
        description="Read a project.",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda project_pk: {"project": project_pk},
            permission_classes=[AllowAny],
        ),
        input_serializer=_Input,
        url_kwargs=(UrlKwarg("project_pk", type="integer"),),
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
        conventions=server.conventions,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["handler", "async_handler", "acall_tool"])
async def test_a_namesake_default_that_queries_fills_the_route_on_every_route(route: str) -> None:
    server = _server()
    params: dict[str, Any] = {"name": "tool", "arguments": {}}
    if route == "acall_tool":
        out: Any = await server.acall_tool("tool", {}, user=None)
    elif route == "async_handler":
        out = await handle_tools_call_async(params, _ctx(server))
    else:
        out = await sync_to_async(handle_tools_call)(params, _ctx(server))

    assert isinstance(out, dict), f"answered {out!r}"
    assert out.get("structuredContent") == {"project": 7}
