"""A retry's answer changing any argument is judged on the arguments it produces.

``tools/call`` judges the binding's permissions on the arguments as sent, merges
a retry's ``inputResponses`` over them, and judged again only when the answers
moved a URL kwarg. Once the stand-in carried ``request.data`` and
``request.query_params`` as the dispatch view does, an answer changing only
those was never judged again: a per-binding ``DRFPermissionAdapter`` admitting
only project ``"7"`` refused ``{"project": "8"}`` sent plainly, and ran the
service on project ``"8"`` sent as ``"7"`` with ``"8"`` answered, on either
handler and over a stream. A spec's own ``permission_classes`` were judged again
by the dispatch view, but only after the rate limit had charged the call.

An answer is merged even with no ``requestState``, so a first call can carry
one. The answers are now judged whenever they change the arguments, before the
rate limit is charged.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import DRFPermissionAdapter, MCPServer, QueryParam
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import ELICITATION_KEY, JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import RefusingRateLimit

_MODERN = "2026-07-28"


class _Args(serializers.Serializer):
    project = serializers.CharField()


class _OnlyProject7(BasePermission):
    """The docs' shape: a DRF class admitting a call whose arguments name project 7."""

    def has_permission(self, request: Any, view: Any) -> bool:
        return request.data.get("project") == "7"


class _OnlyFields7(BasePermission):
    """Admits a call whose routed ``fields`` query value is 7."""

    def has_permission(self, request: Any, view: Any) -> bool:
        return request.query_params.get("fields") == "7"


def _server(
    permission: type[BasePermission],
    ran: list[Any],
    *,
    query: bool = False,
    on_spec: bool = False,
    limiter: Any = None,
) -> MCPServer:
    """``tool`` behind ``permission``, per binding unless ``on_spec``."""

    def _service(data: Any = None) -> dict[str, Any]:
        ran.append(dict(data))
        return {"ok": True}

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_service_tool(
        name="tool",
        description="Touch a project.",
        spec=ServiceSpec(
            service=_service,
            input_serializer=_Args,
            atomic=False,
            permission_classes=[permission] if on_spec else None,
        ),
        permissions=[] if on_spec else [DRFPermissionAdapter(permission)],
        query_params=(QueryParam("fields"),) if query else (),
        rate_limits=[limiter] if limiter is not None else None,
    )
    return server


def _params(arguments: dict[str, Any], answer: dict[str, Any] | None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": "tool",
        "arguments": arguments,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": _MODERN,
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }
    if answer is not None:
        params["inputResponses"] = {ELICITATION_KEY: {"action": "accept", "content": answer}}
    return params


def _body(params: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})


_HEADERS = {"Mcp-Protocol-Version": _MODERN, "Mcp-Method": "tools/call", "Mcp-Name": "tool"}


async def _post(server: MCPServer, params: dict[str, Any], *, is_async: bool) -> Any:
    """The status and JSON-RPC envelope a wire ``tools/call`` answers."""
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        if is_async:
            response = await AsyncClient().post(
                "/mcp/", data=_body(params), content_type="application/json", headers=_HEADERS
            )
        else:
            response = await sync_to_async(Client().post)(
                "/mcp/", data=_body(params), content_type="application/json", headers=_HEADERS
            )
    return response.status_code, json.loads(response.content)


# (arguments as sent, the answer, the permission, whether the value is a query param)
_SMUGGLED: list[tuple[dict[str, Any], dict[str, Any], type[BasePermission], bool]] = [
    ({"project": "7"}, {"project": "8"}, _OnlyProject7, False),
    ({"project": "1", "fields": "7"}, {"fields": "8"}, _OnlyFields7, True),
]
_SMUGGLED_IDS = ["request-data", "query-param"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(("sent", "answer", "permission", "query"), _SMUGGLED, ids=_SMUGGLED_IDS)
async def test_an_answer_changing_a_value_the_permission_reads_is_refused(
    is_async: bool,
    sent: dict[str, Any],
    answer: dict[str, Any],
    permission: type[BasePermission],
    query: bool,
) -> None:
    ran: list[Any] = []
    server = _server(permission, ran, query=query)

    # Sent plainly, the answered value is refused.
    plain = await _post(server, _params({**sent, **answer}, None), is_async=is_async)
    assert plain[0] == 403
    assert plain[1]["error"]["code"] == JsonRpcErrorCode.FORBIDDEN

    # Answered over an admitted value, it is refused the same way.
    status, envelope = await _post(server, _params(sent, answer), is_async=is_async)

    assert status == 403, envelope
    assert envelope["error"]["code"] == JsonRpcErrorCode.FORBIDDEN
    assert ran == []


@pytest.mark.django_db(transaction=True)
async def test_a_streamed_answer_changing_request_data_is_refused() -> None:
    # The pre-flight judges the arguments as sent, before any answer is read,
    # and admits; the stream's handler merges the answer and must judge it.
    ran: list[Any] = []
    server = _server(_OnlyProject7, ran)
    params = _params({"project": "7"}, {"project": "8"})
    params["_meta"]["progressToken"] = "abc"

    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        response = await AsyncClient().post(
            "/mcp/", data=_body(params), content_type="application/json", headers=_HEADERS
        )
        assert response["Content-Type"].startswith("text/event-stream"), response.content[:300]
        body = b"".join([chunk async for chunk in response]).decode()

    frames = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert "error" in frames[-1], f"served {frames[-1]!r}"
    assert frames[-1]["error"]["code"] == JsonRpcErrorCode.FORBIDDEN
    assert ran == []


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=_MODERN,
        conventions=server.conventions,
    )


async def _handle(server: MCPServer, params: dict[str, Any], *, is_async: bool) -> Any:
    if is_async:
        return await handle_tools_call_async(params, _ctx(server))
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("on_spec", [False, True], ids=["per-binding", "spec"])
@pytest.mark.parametrize(("sent", "answer", "permission", "query"), _SMUGGLED, ids=_SMUGGLED_IDS)
async def test_a_caller_refused_on_an_answered_value_is_not_charged(
    is_async: bool,
    on_spec: bool,
    sent: dict[str, Any],
    answer: dict[str, Any],
    permission: type[BasePermission],
    query: bool,
) -> None:
    # A spec's own class was judged again by the dispatch view, so it refused,
    # but after the rate limit had charged a unit; against a spent quota the
    # caller was told ``RATE_LIMITED`` rather than that the value is not its to
    # name. A per-binding class was not judged again at all.
    limit = RefusingRateLimit()
    ran: list[Any] = []
    server = _server(permission, ran, query=query, on_spec=on_spec, limiter=limit)

    out = await _handle(server, _params(sent, answer), is_async=is_async)

    assert isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN, out
    assert limit.consumed == 0
    assert ran == []
