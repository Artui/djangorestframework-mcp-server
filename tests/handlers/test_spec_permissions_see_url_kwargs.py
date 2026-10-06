"""A spec permission reading ``view.kwargs`` judges the route a ``tools/call`` names.

A registered ``UrlKwarg`` reaches ``view.kwargs``, and a permission class scoping
by a route capture reads it there, as it does over HTTP. On ``tools/call`` the
spec's ``permission_classes`` were first judged against a stand-in view whose
``kwargs`` were always ``{}``, before the call's URL kwargs were split out of its
arguments, so such a permission denied a caller it admits and the call was
answered ``-32006``. ``call_tool`` already split first; every other route now
does too, without changing which refusal a call missing a required URL kwarg
gets: the permission still answers before the missing argument does.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import ELICITATION_KEY, JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer
from tests.utils import granting_route, tool_error

# Every entry point a tool call takes. ``call_tool`` was already right and is
# here as the reference the other three are held to.
_ROUTES = ["handler", "async_handler", "call_tool", "acall_tool"]
_KINDS = ["service", "selector"]


def _archive() -> dict[str, Any]:
    return {"archived": True}


def _invoices() -> Any:
    return Invoice.objects.all()


def _server(kind: str, permission: type[BasePermission], *url_kwargs: UrlKwarg) -> MCPServer:
    """One tool named ``tool``, of ``kind``, behind ``permission`` and routed by ``url_kwargs``."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    if kind == "service":
        server.register_service_tool(
            name="tool",
            description="Archive a project.",
            spec=ServiceSpec(service=_archive, permission_classes=[permission]),
            url_kwargs=url_kwargs,
        )
    else:
        server.register_selector_tool(
            name="tool",
            description="List a project's invoices.",
            paginate=True,
            spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_invoices,
                output_serializer=InvoiceOutputSerializer,
                permission_classes=[permission],
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
        protocol_version="2025-11-25",
        conventions=server.conventions,
    )


async def _handler(server: MCPServer, params: dict[str, Any], *, is_async: bool) -> Any:
    if is_async:
        return await handle_tools_call_async(params, _ctx(server))
    # Off the event loop, where the sync handler's ORM work is allowed.
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


async def _via(server: MCPServer, route: str, arguments: dict[str, Any]) -> Any:
    """What calling ``tool`` with ``arguments`` through ``route`` answers.

    ``call_tool`` raises ``PermissionDenied`` where the wire answers ``-32006``,
    so a denial is returned as that error here and every route reads alike.
    """
    if route == "call_tool":
        try:
            result: Any = await sync_to_async(server.call_tool)("tool", arguments, user=None)
        except PermissionDenied:
            return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
        return result.to_dict()
    if route == "acall_tool":
        return await server.acall_tool("tool", arguments, user=None)
    params: dict[str, Any] = {"name": "tool", "arguments": arguments}
    return await _handler(server, params, is_async=route == "async_handler")


def _forbidden(out: Any) -> bool:
    return isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_spec_permission_sees_the_url_kwargs_the_call_delivers(
    route: str, kind: str
) -> None:
    seen: list[dict[str, Any]] = []
    server = _server(
        kind,
        granting_route("project_pk", 7, seen),
        UrlKwarg("project_pk", type="integer", required=True),
    )

    out = await _via(server, route, {"project_pk": 7})

    assert not isinstance(out, JsonRpcError), f"refused: {out!r}"
    assert out.get("isError") is not True
    # Every look it took, up front and on the resolved target alike.
    assert seen
    assert all(kwargs == {"project_pk": 7} for kwargs in seen)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_spec_permission_still_denies_a_route_it_does_not_grant(
    route: str, kind: str
) -> None:
    # The other half: the permission reads the capture rather than granting
    # every call, so the test above holds the value it was shown.
    seen: list[dict[str, Any]] = []
    server = _server(
        kind,
        granting_route("project_pk", 7, seen),
        UrlKwarg("project_pk", type="integer", required=True),
    )

    out = await _via(server, route, {"project_pk": 8})

    assert _forbidden(out)
    assert seen == [{"project_pk": 8}]


def _missing_pk(out: Any) -> dict[str, Any]:
    """The detail of the ``isError`` result a missing ``project_pk`` earns."""
    assert not isinstance(out, JsonRpcError), f"answered as a protocol error: {out!r}"
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["message"] == "Missing required argument(s): `project_pk`."
    return error["detail"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_permission_granting_the_delivered_route_is_followed_by_the_missing_argument(
    route: str, kind: str
) -> None:
    # ``project_pk`` is required and missing, ``tenant`` delivered. The split
    # that runs before the permission refuses nothing, so the permission sees
    # the ``tenant`` the call named, grants, and the missing argument is what
    # the caller is told -- the order a missing URL kwarg has always had.
    seen: list[dict[str, Any]] = []
    server = _server(
        kind,
        granting_route("tenant", "acme", seen),
        UrlKwarg("project_pk", type="integer", required=True),
        UrlKwarg("tenant"),
    )

    out = await _via(server, route, {"tenant": "acme"})

    assert _missing_pk(out) == {"project_pk": ["This field is required."]}
    assert seen == [{"tenant": "acme"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_permission_denying_the_delivered_route_answers_before_the_missing_argument(
    route: str, kind: str
) -> None:
    # A caller the permission refuses is told so, not which argument it left
    # out: splitting earlier moved the permission's view, not its place.
    seen: list[dict[str, Any]] = []
    server = _server(
        kind,
        granting_route("tenant", "acme", seen),
        UrlKwarg("project_pk", type="integer", required=True),
        UrlKwarg("tenant"),
    )

    out = await _via(server, route, {"tenant": "beta"})

    assert _forbidden(out)
    assert seen == [{"tenant": "beta"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_an_answer_that_changes_the_route_is_judged_again_before_the_service_runs(
    is_async: bool,
) -> None:
    # The permission is judged on the arguments as sent, before a retry's
    # ``inputResponses`` are merged over them, because a denied caller is told
    # so before its answers are read. An answer naming a different capture is
    # still judged: the target guard runs the class-level check again against
    # the view the service is dispatched with.
    ran: list[bool] = []

    def _archive_project() -> dict[str, Any]:
        ran.append(True)
        return {"archived": True}

    seen: list[dict[str, Any]] = []
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="tool",
        description="Archive a project.",
        spec=ServiceSpec(
            service=_archive_project, permission_classes=[granting_route("project_pk", 7, seen)]
        ),
        url_kwargs=(UrlKwarg("project_pk", type="integer", required=True),),
    )
    params: dict[str, Any] = {
        "name": "tool",
        "arguments": {"project_pk": 7},
        "inputResponses": {ELICITATION_KEY: {"action": "accept", "content": {"project_pk": 8}}},
    }

    out = await _handler(server, params, is_async=is_async)

    assert _forbidden(out)
    assert seen == [{"project_pk": 7}, {"project_pk": 8}]
    assert ran == []
