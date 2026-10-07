"""A permission writing into ``request.data`` reaches neither the next check nor the caller.

Each check a ``tools/call`` makes is built from a shape split out of the
call's arguments, and ``view.kwargs`` was copied per build. ``request.data``
was not: ``split_url_kwargs`` and ``split_query_params`` hand back the mapping
they were given when the binding declares nothing to split, so on a tool with
no URL kwarg and no ``QueryParam``, and on every chain, ``request.data`` was the
caller's own ``arguments``. A permission stamping a value into it, as some
codebases do from ``has_permission``, carried that value into the dispatch
view's check and into the mapping the caller passed in.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.types.chain_step import ChainStep


class _Note(serializers.Serializer):
    note = serializers.CharField(required=False)
    # Declared so the dispatch view's own stamp, which is the request the
    # service runs with as DRF's serializer would read it, is not refused as an
    # unexpected argument: what is asserted here is where a stamp travels.
    owner = serializers.CharField(required=False)


class _Touched(serializers.Serializer):
    touched = serializers.BooleanField()


def _stamping(saw: list[bool]) -> type[BasePermission]:
    """Records whether a stamp is already there, then stamps the caller into ``request.data``."""

    class _StampsTheOwner(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            saw.append("owner" in request.data)
            request.data["owner"] = "stamped"
            return True

    return _StampsTheOwner


def _server(kind: str, permission: type[BasePermission]) -> MCPServer:
    """``tool`` of ``kind`` behind ``permission``, declaring no URL kwarg and no ``QueryParam``."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    if kind == "service":
        server.register_service_tool(
            name="tool",
            description="Touch a note.",
            spec=ServiceSpec(
                service=lambda data=None: {"touched": True},
                input_serializer=_Note,
                atomic=False,
                permission_classes=[permission],
            ),
        )
    elif kind == "selector":
        server.register_selector_tool(
            name="tool",
            description="Read a note.",
            spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda note=None: {"touched": True},
                output_serializer=_Touched,
                permission_classes=[permission],
            ),
        )
    else:
        server.register_chain_tool(
            name="tool",
            description="Touch a note.",
            input_serializer=_Note,
            steps=[
                ChainStep(
                    "touch",
                    ServiceSpec(
                        service=lambda: {"touched": True},
                        atomic=False,
                        permission_classes=[permission],
                    ),
                )
            ],
        )
    return server


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("kind", ["service", "selector", "chain"])
async def test_a_permission_writing_request_data_reaches_neither_the_next_check_nor_the_caller(
    kind: str, is_async: bool
) -> None:
    saw: list[bool] = []
    server = _server(kind, _stamping(saw))
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2026-07-28",
        conventions=server.conventions,
    )
    arguments: dict[str, Any] = {"note": "x"}
    params: dict[str, Any] = {"name": "tool", "arguments": arguments}

    if is_async:
        out: Any = await handle_tools_call_async(params, context)
    else:
        out = await sync_to_async(handle_tools_call)(params, context)

    assert out.get("isError") is not True, out
    # The binding's stand-in, then the dispatch view (a chain step's own view):
    # neither finds the other's stamp.
    assert saw == [False, False]
    assert arguments == {"note": "x"}
