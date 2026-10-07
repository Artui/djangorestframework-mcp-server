"""The binding's stand-in sees what the spec's dispatch view sees.

A spec's ``permission_classes`` are judged twice on ``tools/call``: by the
binding's wrapped copy, against a stand-in, then by ``enforce_permissions``,
against the dispatch view. The stand-in's ``request.data`` parsed the JSON-RPC
body with no parsers, so a permission reading it raised
``UnsupportedMediaType`` and every wire call was an HTTP 500; its
``query_params`` were the endpoint's query string; its ``view.action`` was
``None``. Both are now built from one shape, so they see one request.

Every call here goes through the Django test client, as a real one does,
because a bare ``HttpRequest`` has no body to fail to parse.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, QueryParam, TaskPolicy, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import TASKS_EXTENSION_ID, JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.tasks.conftest import RecordingExecutor
from tests.testapp.urlconf_for import urlconf_for

_MODERN = "2026-07-28"
_ROUTES = ["sync_wire", "async_wire", "acall_tool"]
_KINDS = ["service", "selector"]


class _CountingRateLimit:
    """An ``MCPRateLimit`` admitting every call, counting the calls it charged."""

    def __init__(self) -> None:
        self.consumed: int = 0

    def consume(self, request: Any, token: Any) -> int | None:
        self.consumed += 1
        return None


class _Note(serializers.Serializer):
    note = serializers.CharField(required=False)


def _touch(data: Any = None) -> dict[str, Any]:
    return {"touched": True}


def _read(note: Any = None) -> dict[str, Any]:
    return {"touched": True}


class _Touched(serializers.Serializer):
    touched = serializers.BooleanField()


def _server(
    kind: str,
    permission: type[BasePermission],
    *,
    name: str = "tool",
    limiter: Any = None,
    task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
    task_store: Any = None,
    executor: Any = None,
) -> MCPServer:
    """One tool of ``kind`` behind ``permission``, routed by ``tenant`` and shaped by ``project``.

    ``tenant`` is a URL kwarg, ``project`` a ``QueryParam`` and ``note`` an
    ordinary argument, so each lands in a channel of its own.
    """
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        task_store=task_store,
        task_executor=executor,
    )
    common: dict[str, Any] = {
        "name": name,
        "url_kwargs": (UrlKwarg("tenant", type="integer"),),
        "query_params": (QueryParam("project"),),
        "rate_limits": [limiter] if limiter is not None else None,
        "task_policy": task_policy,
    }
    if kind == "service":
        server.register_service_tool(
            description="Touch a project.",
            spec=ServiceSpec(
                service=_touch,
                input_serializer=_Note,
                atomic=False,
                permission_classes=[permission],
            ),
            **common,
        )
    else:
        server.register_selector_tool(
            description="Read a project.",
            spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_read,
                output_serializer=_Touched,
                permission_classes=[permission],
            ),
            **common,
        )
    return server


def _body(name: str, arguments: dict[str, Any], **meta: Any) -> str:
    params: dict[str, Any] = {
        "name": name,
        "arguments": arguments,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": _MODERN,
            "io.modelcontextprotocol/clientCapabilities": {},
            **meta,
        },
    }
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})


def _headers(name: str) -> dict[str, str]:
    return {"Mcp-Protocol-Version": _MODERN, "Mcp-Method": "tools/call", "Mcp-Name": name}


async def _call(
    server: MCPServer, route: str, arguments: dict[str, Any], *, name: str = "tool"
) -> Any:
    """What calling ``name`` through ``route`` answers: a result dict or a ``JsonRpcError``.

    The wire's error envelope is read back into a ``JsonRpcError``, so every
    route answers in one shape; a denial is a ``403`` there. A 500 fails the
    call outright.
    """
    if route == "acall_tool":
        return await server.acall_tool(name, arguments, user=None)
    if route == "call_tool":
        try:
            result: Any = await sync_to_async(server.call_tool)(name, arguments, user=None)
        except PermissionDenied:
            return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
        return result.to_dict()
    is_async: bool = route == "async_wire"
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        if is_async:
            response = await AsyncClient().post(
                "/mcp/?project=99",
                data=_body(name, arguments),
                content_type="application/json",
                headers=_headers(name),
            )
        else:
            response = await sync_to_async(Client().post)(
                "/mcp/?project=99",
                data=_body(name, arguments),
                content_type="application/json",
                headers=_headers(name),
            )
    assert response.status_code in (200, 403), response.content[:300]
    payload: dict[str, Any] = json.loads(response.content)
    if "error" in payload:
        return JsonRpcError(JsonRpcErrorCode(payload["error"]["code"]), payload["error"]["message"])
    return payload["result"]


def _assert_served(out: Any) -> None:
    assert not isinstance(out, JsonRpcError), f"answered {out!r}"
    assert out.get("isError") is not True, out
    assert out["structuredContent"] == {"touched": True}


def _assert_denied(out: Any) -> None:
    assert isinstance(out, JsonRpcError), f"served {out!r}"
    assert out.code == JsonRpcErrorCode.FORBIDDEN


# ----- each value the stand-in reads, admitting one call and denying another -----


class _ScopesByBody(BasePermission):
    """Admits a call whose arguments name note ``"7"``."""

    def has_permission(self, request: Any, view: Any) -> bool:
        return request.data.get("note") == "7"


class _ScopesByQuery(BasePermission):
    """Admits a call whose routed ``project`` is ``7``; the endpoint's query string says 99."""

    def has_permission(self, request: Any, view: Any) -> bool:
        return request.query_params.get("project") == "7"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_permission_reading_request_data_admits_and_denies_by_the_arguments(
    route: str, kind: str
) -> None:
    limiter = _CountingRateLimit()
    server = _server(kind, _ScopesByBody, limiter=limiter)

    _assert_served(await _call(server, route, {"note": "7"}))
    assert limiter.consumed == 1
    _assert_denied(await _call(server, route, {"note": "8"}))
    # Denied before the rate limit is charged.
    assert limiter.consumed == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_permission_reading_query_params_admits_and_denies_by_the_routed_value(
    route: str, kind: str
) -> None:
    limiter = _CountingRateLimit()
    server = _server(kind, _ScopesByQuery, limiter=limiter)

    _assert_served(await _call(server, route, {"project": 7}))
    assert limiter.consumed == 1
    _assert_denied(await _call(server, route, {"project": 8}))
    assert limiter.consumed == 1


class _AdmitsTheToolAction(BasePermission):
    """Admits the action ``tool``, which is the dispatch view's for a tool of that name."""

    def has_permission(self, request: Any, view: Any) -> bool:
        return view.action == "tool"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_permission_reading_view_action_admits_and_denies_by_the_tool(
    route: str, kind: str
) -> None:
    limiter = _CountingRateLimit()
    admitted = _server(kind, _AdmitsTheToolAction, limiter=limiter)
    denied = _server(kind, _AdmitsTheToolAction, name="other", limiter=limiter)

    _assert_served(await _call(admitted, route, {}))
    assert limiter.consumed == 1
    _assert_denied(await _call(denied, route, {}, name="other"))
    assert limiter.consumed == 1


# ----- the stand-in and the dispatch view are one request -----


def _recording(seen: list[tuple[Any, ...]]) -> type[BasePermission]:
    """A permission admitting everything and recording the request and view of each check."""

    class _Recording(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(
                (
                    view.action,
                    dict(view.kwargs),
                    dict(request.data),
                    dict(request.query_params.lists()),
                    request.method,
                )
            )
            return True

    return _Recording


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_the_stand_in_sees_what_the_dispatch_view_sees(route: str, kind: str) -> None:
    seen: list[tuple[Any, ...]] = []
    server = _server(kind, _recording(seen))

    _assert_served(await _call(server, route, {"tenant": 3, "project": 7, "note": "x"}))

    # The stand-in's check, then the dispatch view's: the route in
    # ``view.kwargs``, the routed query value (not the endpoint's 99) in
    # ``query_params``, the rest in ``data``, and the tool's name.
    expected = ("tool", {"tenant": 3}, {"note": "x"}, {"project": ["7"]}, "POST")
    assert seen == [expected, expected]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", [*_ROUTES, "call_tool"])
async def test_request_data_holds_no_route_or_query_value(route: str, kind: str) -> None:
    # DRF's layout on every route: a route capture in ``view.kwargs``, a query
    # value in ``query_params``, the rest in ``data``. A selector tool's
    # dispatch view once carried the first two in ``data`` as well.
    seen: list[tuple[Any, ...]] = []
    server = _server(kind, _recording(seen))

    _assert_served(await _call(server, route, {"tenant": 3, "project": 7, "note": "x"}))

    assert seen, "no check was made"
    assert [data for _action, _kwargs, data, _query, _method in seen] == [{"note": "x"}] * len(seen)


# ----- a streamed call and a task-augmented call -----


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
async def test_a_streamed_call_is_judged_on_its_arguments(kind: str) -> None:
    # The pre-flight judges the binding's permissions before a stream commits
    # its status. An admitted caller is streamed its result; a denied one is
    # still answered ``403`` before any stream exists.
    server = _server(kind, _ScopesByBody)

    async def _stream(note: str) -> Any:
        with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
            return await AsyncClient().post(
                "/mcp/",
                data=_body("tool", {"note": note}, progressToken="abc"),
                content_type="application/json",
                headers=_headers("tool"),
            )

    admitted = await _stream("7")
    assert admitted.status_code == 200
    assert admitted["Content-Type"].startswith("text/event-stream")
    body = b"".join([chunk async for chunk in admitted]).decode()
    frames = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert frames[-1]["result"]["structuredContent"] == {"touched": True}

    denied = await _stream("8")
    assert denied.status_code == 403
    assert json.loads(denied.content)["error"]["code"] == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
async def test_a_streamed_call_asks_once_more_in_its_preflight_and_sees_the_same_request(
    kind: str,
) -> None:
    # The pre-flight, the stand-in and the dispatch view: three checks, each
    # reading the request the call runs with.
    seen: list[tuple[Any, ...]] = []
    server = _server(kind, _recording(seen))

    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        response = await AsyncClient().post(
            "/mcp/?project=99",
            data=_body("tool", {"tenant": 3, "project": 7, "note": "x"}, progressToken="abc"),
            content_type="application/json",
            headers=_headers("tool"),
        )
        # A refusal or an error is answered before any stream exists.
        assert response["Content-Type"].startswith("text/event-stream"), response.content[:300]
        _ = [chunk async for chunk in response]

    expected = ("tool", {"tenant": 3}, {"note": "x"}, {"project": ["7"]}, "POST")
    assert seen == [expected] * 3


@pytest.mark.django_db
@pytest.mark.parametrize("kind", _KINDS)
def test_a_task_is_judged_on_the_arguments_it_would_run_with(kind: str) -> None:
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = _server(
        kind, _ScopesByBody, task_policy=TaskPolicy.REQUIRED, task_store=store, executor=executor
    )

    def _create(note: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": "tool",
            "arguments": {"note": note},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": _MODERN,
                "io.modelcontextprotocol/clientCapabilities": {
                    "extensions": {TASKS_EXTENSION_ID: {}}
                },
            },
        }
        with override_settings(ROOT_URLCONF=urlconf_for(server)):
            response = Client().post(
                "/mcp/",
                data=json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
                ),
                content_type="application/json",
                headers=_headers("tool"),
            )
        assert response.status_code in (200, 403), response.content[:300]
        return json.loads(response.content)

    admitted = _create("7")
    assert admitted["result"]["resultType"] == "task", admitted
    assert len(executor.enqueued) == 1

    denied = _create("8")
    assert denied["error"]["code"] == JsonRpcErrorCode.FORBIDDEN
    assert len(executor.enqueued) == 1


# ----- a listing names no call -----


@pytest.mark.django_db
@pytest.mark.parametrize("kind", _KINDS)
def test_a_listing_filtered_by_permission_judges_a_request_with_no_arguments(
    kind: str, settings: Any
) -> None:
    # ``tools/list`` names no call, so a body-reading permission is judged
    # against ``{}`` there rather than against the JSON-RPC body it raised on.
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    seen: list[tuple[Any, ...]] = []
    server = _server(kind, _recording(seen))
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": _MODERN,
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        }
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        response = Client().post(
            "/mcp/",
            data=body,
            content_type="application/json",
            headers={"Mcp-Protocol-Version": _MODERN, "Mcp-Method": "tools/list"},
        )

    assert response.status_code == 200, response.content[:300]
    assert [tool["name"] for tool in json.loads(response.content)["result"]["tools"]] == ["tool"]
    assert [(action, data) for action, _kwargs, data, _query, _method in seen] == [(None, {})]


# ----- ``request.auth`` keeps the caller on the dispatch view too -----


class _User:
    pk = 1
    is_authenticated = True


# What a token backend publishes as ``TokenInfo.raw``: compared by identity, so
# a site passing ``None`` in its place cannot pass for it.
_AUTH: object = object()


def _reading_auth_then_user(saw: list[Any]) -> type[BasePermission]:
    """``TokenHasScope``'s order: ``request.auth`` first, then ``request.user``."""

    class _ReadsAuthThenUser(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            saw.append(request.auth)
            return bool(request.user and request.user.is_authenticated)

    return _ReadsAuthThenUser


def _auth_server(kind: str, permission: type[BasePermission]) -> MCPServer:
    """``tool`` of ``kind`` behind ``permission``; a chain's one step carries it."""
    if kind != "chain":
        return _server(kind, permission)
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_chain_tool(
        name="tool",
        description="Touch a project.",
        steps=[
            ChainStep(
                "touch",
                ServiceSpec(service=_touch, atomic=False, permission_classes=[permission]),
            )
        ],
    )
    return server


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", [*_KINDS, "chain"])
@pytest.mark.parametrize("route", ["sync_handler", "async_handler"])
async def test_a_dispatch_view_reading_auth_keeps_the_caller(route: str, kind: str) -> None:
    # Reading ``request.auth`` on a request that never authenticated runs
    # DRF's empty authenticator chain, which resets ``request.user`` to
    # ``AnonymousUser``. The stand-in set ``auth`` and admitted; the dispatch
    # view did not, and denied the same authenticated caller. Both checks see
    # the backend's own payload, not merely something that is not a trigger:
    # each site passing ``auth=None`` keeps the caller too, and is caught here.
    saw: list[Any] = []
    server = _auth_server(kind, _reading_auth_then_user(saw))
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=_User(), raw=_AUTH),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=_MODERN,
        conventions=server.conventions,
    )
    params: dict[str, Any] = {"name": "tool", "arguments": {}}
    if route == "async_handler":
        out: Any = await handle_tools_call_async(params, context)
    else:
        out = await sync_to_async(handle_tools_call)(params, context)

    _assert_served(out)
    # The binding's stand-in, then the dispatch view (a chain step's own view).
    assert len(saw) == 2
    assert all(auth is _AUTH for auth in saw), saw


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", ["acall_tool", "call_tool"])
async def test_an_in_process_call_reading_auth_keeps_the_caller(route: str, kind: str) -> None:
    # ``call_tool`` and ``acall_tool`` publish no token payload, so
    # ``request.auth`` is ``None``: a value, not a trigger that resets the user.
    saw: list[Any] = []
    server = _server(kind, _reading_auth_then_user(saw))
    user = _User()
    if route == "acall_tool":
        out: Any = await server.acall_tool("tool", {}, user=user)
    else:
        out = (await sync_to_async(server.call_tool)("tool", {}, user=user)).to_dict()

    _assert_served(out)
    assert saw
    assert all(auth is None for auth in saw), saw
