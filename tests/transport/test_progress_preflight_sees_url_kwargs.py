"""The permission pre-flight of a streamed ``tools/call`` judges the route the call names.

A call asking for progress is answered by a stream, and a stream commits its
status before the handler runs, so the transport judges the tool's permissions
first, to keep a denial a ``403``. That pre-flight judged a spec's permission
classes against a stand-in view whose ``kwargs`` were always ``{}``, so a
permission scoping by a route capture refused, with a ``403``, a streamed call
that the same call without a progress token was admitted to.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import AsyncClient, override_settings
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from rest_framework_mcp.transport.progress_dispatch import preflight_permissions
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import granting_route

_MODERN = "2026-07-28"
_KINDS = ["service", "selector"]


def _archive() -> dict[str, Any]:
    return {"archived": True}


def _project() -> dict[str, Any]:
    return {"name": "Apollo"}


def _server(kind: str, permission: type[BasePermission], *url_kwargs: UrlKwarg) -> MCPServer:
    """One tool named ``tool``, of ``kind``, behind ``permission`` and routed by ``url_kwargs``."""
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    if kind == "service":
        server.register_service_tool(
            name="tool",
            description="Archive a project.",
            spec=ServiceSpec(service=_archive, atomic=False, permission_classes=[permission]),
            url_kwargs=url_kwargs,
        )
    else:
        server.register_selector_tool(
            name="tool",
            description="Read a project.",
            spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_project, permission_classes=[permission]
            ),
            url_kwargs=url_kwargs,
        )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=_MODERN,
        config=server.config,
    )


def _preflight(server: MCPServer, params: dict[str, Any]) -> JsonRpcError | None:
    return preflight_permissions("tools/call", {"name": "tool", **params}, _ctx(server))


@pytest.mark.parametrize("kind", _KINDS)
def test_the_preflight_sees_the_url_kwargs_the_call_delivers(kind: str) -> None:
    seen: list[dict[str, Any]] = []
    server = _server(
        kind,
        granting_route("project_pk", 7, seen),
        UrlKwarg("project_pk", type="integer", required=True),
    )

    assert _preflight(server, {"arguments": {"project_pk": 7}}) is None
    assert seen == [{"project_pk": 7}]


@pytest.mark.parametrize("kind", _KINDS)
def test_the_preflight_still_denies_a_route_it_does_not_grant(kind: str) -> None:
    seen: list[dict[str, Any]] = []
    server = _server(
        kind,
        granting_route("project_pk", 7, seen),
        UrlKwarg("project_pk", type="integer", required=True),
    )

    denied = _preflight(server, {"arguments": {"project_pk": 8}})

    assert isinstance(denied, JsonRpcError)
    assert denied.code == JsonRpcErrorCode.FORBIDDEN
    assert seen == [{"project_pk": 8}]


@pytest.mark.parametrize("kind", _KINDS)
def test_a_denied_caller_missing_a_url_kwarg_is_refused_by_the_preflight(kind: str) -> None:
    # ``project_pk`` is required and missing. The pre-flight's split refuses
    # nothing, so the permission judges the ``tenant`` the call named and its
    # denial is the ``403``; a caller it admits goes on to the handler, which
    # names the missing argument, as it always has.
    seen: list[dict[str, Any]] = []
    server = _server(
        kind,
        granting_route("tenant", "acme", seen),
        UrlKwarg("project_pk", type="integer", required=True),
        UrlKwarg("tenant"),
    )

    denied = _preflight(server, {"arguments": {"tenant": "beta"}})
    admitted = _preflight(server, {"arguments": {"tenant": "acme"}})

    assert isinstance(denied, JsonRpcError)
    assert denied.code == JsonRpcErrorCode.FORBIDDEN
    assert admitted is None
    assert seen == [{"tenant": "beta"}, {"tenant": "acme"}]


@pytest.mark.parametrize("arguments", [{}, {"arguments": None}, {"arguments": ["acme"]}])
def test_a_call_with_no_arguments_object_is_judged_on_the_route_defaults(
    arguments: dict[str, Any],
) -> None:
    # The pre-flight runs before the handler validates ``arguments``, so it
    # meets one that is absent, ``null`` or not an object. It judges those as
    # delivering nothing, as the handler reads an absent one, rather than
    # failing the request before the handler can name the fault; a declared
    # default is what the dispatch would put in ``view.kwargs`` then.
    seen: list[dict[str, Any]] = []
    server = _server(
        "service", granting_route("tenant", "acme", seen), UrlKwarg("tenant", default="acme")
    )

    assert _preflight(server, arguments) is None
    assert seen == [{"tenant": "acme"}]


@pytest.mark.django_db(transaction=True)
async def test_a_streamed_call_to_a_route_its_permission_grants_is_streamed_not_403() -> None:
    server = _server(
        "service",
        granting_route("project_pk", 7, []),
        UrlKwarg("project_pk", type="integer", required=True),
    )
    params: dict[str, Any] = {
        "name": "tool",
        "arguments": {"project_pk": 7},
        "_meta": {
            "progressToken": "abc",
            "io.modelcontextprotocol/protocolVersion": _MODERN,
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        response = await AsyncClient().post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}),
            content_type="application/json",
            headers={
                "Mcp-Protocol-Version": _MODERN,
                "Mcp-Method": "tools/call",
                "Mcp-Name": "tool",
            },
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/event-stream")
        body = b"".join([chunk async for chunk in response]).decode()

    frames = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert frames[-1]["result"]["structuredContent"] == {"archived": True}
