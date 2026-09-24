"""An exception escaping dispatch is still answered with a JSON-RPC reply.

Every case goes through the real URL conf, on both viewsets and in both eras,
because the defect lived at the HTTP edge: the handlers were never asked to
catch everything, and before the backstop an escaping DRF ``APIException`` was
rendered by DRF's ``handle_exception`` as a bare DRF body (a ``400`` reading
``["`items` field is not found"]``, with no ``jsonrpc`` and no ``id``), while
anything else became Django's ``500`` page.

The second half pins the responses the backstop must **not** absorb. None of
them is an exception escaping dispatch - each is either a value a handler
returns or a rejection made before dispatch runs - but a backstop placed one
level too wide would turn every one into a ``-32603``, so each is held here on
every path the backstop sits on.

The clients are built with ``raise_request_exception=False`` so that the
pre-backstop behaviour fails these tests on their assertions (a status, a
content type) rather than by re-raising the handler's exception into the test.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.throttling import BaseThrottle
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer, ScopeRequired
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.transport.async_streamable_http_viewset import AsyncStreamableHttpViewSet
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from rest_framework_mcp.transport.streamable_http_viewset import StreamableHttpViewSet
from tests.testapp.urlconf_for import urlconf_for

LEGACY = "2025-11-25"
MODERN = "2026-07-28"
# A string id, so a reply echoing a default ``1`` or ``null`` cannot pass.
REQUEST_ID = "backstop-7"
# What django-restql 0.18.0 raises from ``to_representation`` for a selection
# naming a field the serializer lacks: the reported case, verbatim.
RESTQL_TEXT = "`items` field is not found"
# Stands in for whatever a real exception carries that a client must not read.
SECRET = "could not connect to db-primary.internal:5432"

_VIEWSET_LOGGERS: dict[bool, str] = {
    False: "rest_framework_mcp.transport.streamable_http_viewset",
    True: "rest_framework_mcp.transport.async_streamable_http_viewset",
}
_STREAM_LOGGER = "rest_framework_mcp.transport.response_stream"


# ----- a server whose tools fail in the ways a dispatch can -----


class _RestqlRejectingSerializer(serializers.Serializer):
    """Raises at render time exactly what strict django-restql raises."""

    id = serializers.IntegerField()

    def to_representation(self, instance: Any) -> Any:
        raise ValidationError(RESTQL_TEXT, code="not_found")


def _rows() -> list[dict[str, Any]]:
    return [{"id": 1}, {"id": 2}]


def _explode() -> list[dict[str, Any]]:
    raise RuntimeError(SECRET)


class _ExplodingPermission:
    """A consumer permission class with a bug in it."""

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        raise RuntimeError(SECRET)

    def required_scopes(self) -> list[str]:
        return []


class _RaisingDenial:
    """A permission class that refuses by raising, the DRF idiom for a message."""

    def __init__(self, exc_type: type[Exception]) -> None:
        self.exc_type = exc_type

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        raise self.exc_type(SECRET)

    def required_scopes(self) -> list[str]:
        return []


class _AlwaysDeny:
    """A per-binding rate limit that is always exhausted."""

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        return 42


def _server() -> MCPServer:
    server = MCPServer(
        name="backstop",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        subscription_broker=InMemorySubscriptionBroker(),
    )
    # Paged and declaring no query param: the render-time rejection has no
    # caller-supplied value to blame, so it is a server fault on every
    # release and always reaches the backstop.
    server.register_selector_tool(
        name="boom.render",
        description="Renders through a serializer that rejects.",
        spec=SelectorSpec(
            kind=SelectorKind.LIST, selector=_rows, output_serializer=_RestqlRejectingSerializer
        ),
        paginate=True,
    )
    server.register_selector_tool(
        name="boom.runtime",
        description="Raises from the selector.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_explode),
    )
    server.register_selector_tool(
        name="boom.permission",
        description="Raises from its permission class.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_rows),
        permissions=[_ExplodingPermission()],
    )
    server.register_selector_tool(
        name="ok.gated",
        description="Denied to a caller without the scope.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_rows),
        permissions=[ScopeRequired(["mcp:admin"])],
    )
    server.register_selector_tool(
        name="deny.drf",
        description="Refused by a permission class raising DRF's PermissionDenied.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_rows),
        permissions=[_RaisingDenial(PermissionDenied)],
    )
    server.register_selector_tool(
        name="deny.django",
        description="Refused by a permission class raising Django's PermissionDenied.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_rows),
        permissions=[_RaisingDenial(DjangoPermissionDenied)],
    )
    server.register_selector_tool(
        name="ok.limited",
        description="Always rate limited.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_rows),
        rate_limits=[_AlwaysDeny()],
    )
    server.register_resource(
        name="boom",
        uri_template="boom://thing",
        selector=SelectorSpec(kind=SelectorKind.LIST, selector=_rows),
        permissions=[_ExplodingPermission()],
    )
    return server


# ----- wire helpers -----


def _initialize_body() -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.0"},
            },
        }
    )


def _request(
    method: str,
    params: dict[str, Any],
    *,
    modern: bool,
    progress: bool = False,
    notification: bool = False,
) -> tuple[str, dict[str, str]]:
    """The body and headers of one POST, shaped for its era."""
    meta: dict[str, Any] = {}
    if modern:
        meta["io.modelcontextprotocol/protocolVersion"] = MODERN
        meta["io.modelcontextprotocol/clientCapabilities"] = {}
    if progress:
        meta["progressToken"] = "p1"
    sent: dict[str, Any] = {**params, "_meta": meta} if meta else dict(params)
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": sent}
    if not notification:
        body["id"] = REQUEST_ID
    headers: dict[str, str] = {"Mcp-Protocol-Version": MODERN if modern else LEGACY}
    if modern and not notification:
        headers["Mcp-Method"] = method
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
    return json.dumps(body), headers


def _post_sync(
    server: MCPServer, method: str, params: dict[str, Any], *, modern: bool, **kw: Any
) -> Any:
    client = Client(raise_request_exception=False)
    body, headers = _request(method, params, modern=modern, **kw)
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        if not modern:
            init = client.post("/mcp/", data=_initialize_body(), content_type="application/json")
            headers["Mcp-Session-Id"] = init["Mcp-Session-Id"]
        return client.post("/mcp/", data=body, content_type="application/json", headers=headers)


async def _post_async(
    server: MCPServer, method: str, params: dict[str, Any], *, modern: bool, **kw: Any
) -> Any:
    client = AsyncClient(raise_request_exception=False)
    body, headers = _request(method, params, modern=modern, **kw)
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        if not modern:
            init = await client.post(
                "/mcp/", data=_initialize_body(), content_type="application/json"
            )
            headers["Mcp-Session-Id"] = init["Mcp-Session-Id"]
        return await client.post(
            "/mcp/", data=body, content_type="application/json", headers=headers
        )


async def _post(
    is_async: bool, server: MCPServer, method: str, params: dict[str, Any], **kw: Any
) -> Any:
    if is_async:
        return await _post_async(server, method, params, **kw)
    # Off the loop: the sync test client must not run inside an event loop.
    return await sync_to_async(_post_sync)(server, method, params, **kw)


async def _frames(response: Any) -> list[dict[str, Any]]:
    """Parse the ``data:`` lines of an SSE body into JSON-RPC messages."""
    raw = b"".join([chunk async for chunk in response.streaming_content]).decode()
    return [
        json.loads(line[len("data: ") :]) for line in raw.splitlines() if line.startswith("data: ")
    ]


def _assert_internal_error(response: Any) -> None:
    """A JSON-RPC ``-32603`` for this request, under a ``500``, saying nothing more."""
    assert response["Content-Type"].startswith("application/json"), response.content[:200]
    assert response.status_code == 500, response.content
    body = response.json()
    assert body == {
        "jsonrpc": "2.0",
        "id": REQUEST_ID,
        "error": {"code": -32603, "message": "Internal error"},
    }
    text = response.content.decode()
    assert "field is not found" not in text
    assert "db-primary" not in text
    assert "Error" not in text.replace("Internal error", "")


def _assert_logged(caplog: pytest.LogCaptureFixture, logger: str, exc_type: type) -> None:
    """The operator gets what the client does not: the exception and its traceback."""
    records = [r for r in caplog.records if r.name == logger and r.levelno == logging.ERROR]
    assert len(records) == 1, [r.name for r in caplog.records]
    record = records[0]
    assert REQUEST_ID in record.getMessage()
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], exc_type)


_PATHS = pytest.mark.parametrize(
    ("is_async", "modern"),
    [(False, False), (False, True), (True, False), (True, True)],
    ids=["sync-legacy", "sync-modern", "async-legacy", "async-modern"],
)


# ----- the backstop -----


@_PATHS
async def test_an_escaping_api_exception_is_a_json_rpc_internal_error(
    is_async: bool, modern: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """The reported case: a DRF ``ValidationError`` raised while rendering.

    Before the backstop DRF rendered it as a ``400`` with a bare list for a
    body, which a client cannot parse as JSON-RPC or match to its request.
    """
    caplog.set_level(logging.ERROR, logger="rest_framework_mcp")
    response = await _post(
        is_async, _server(), "tools/call", {"name": "boom.render", "arguments": {}}, modern=modern
    )
    _assert_internal_error(response)
    _assert_logged(caplog, _VIEWSET_LOGGERS[is_async], ValidationError)
    assert RESTQL_TEXT in caplog.text


@_PATHS
async def test_any_other_escaping_exception_is_a_json_rpc_internal_error(
    is_async: bool, modern: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """Before the backstop this was Django's ``500`` page, HTML and all."""
    caplog.set_level(logging.ERROR, logger="rest_framework_mcp")
    response = await _post(
        is_async, _server(), "tools/call", {"name": "boom.runtime", "arguments": {}}, modern=modern
    )
    _assert_internal_error(response)
    _assert_logged(caplog, _VIEWSET_LOGGERS[is_async], RuntimeError)
    assert "db-primary" in caplog.text


@_PATHS
async def test_a_permission_class_that_raises_is_a_json_rpc_internal_error(
    is_async: bool, modern: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """Consumer code raising inside the dispatch's own permission check."""
    caplog.set_level(logging.ERROR, logger="rest_framework_mcp")
    response = await _post(
        is_async,
        _server(),
        "tools/call",
        {"name": "boom.permission", "arguments": {}},
        modern=modern,
    )
    _assert_internal_error(response)
    _assert_logged(caplog, _VIEWSET_LOGGERS[is_async], RuntimeError)


@pytest.mark.parametrize("modern", [False, True], ids=["legacy", "modern"])
async def test_a_permission_preflight_that_raises_before_a_stream_is_a_json_rpc_error(
    modern: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """A ``progressToken`` moves the permission check ahead of the stream.

    It runs before any status is committed, so the backstop can still answer
    with a ``500`` and a JSON body rather than opening a stream.
    """
    caplog.set_level(logging.ERROR, logger="rest_framework_mcp")
    response = await _post_async(
        _server(),
        "tools/call",
        {"name": "boom.permission", "arguments": {}},
        modern=modern,
        progress=True,
    )
    assert not response.streaming
    _assert_internal_error(response)
    _assert_logged(caplog, _VIEWSET_LOGGERS[True], RuntimeError)


async def test_a_subscription_grant_that_raises_is_a_json_rpc_internal_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``subscriptions/listen`` runs resource permission classes to build its grant."""
    caplog.set_level(logging.ERROR, logger="rest_framework_mcp")
    response = await _post_async(
        _server(),
        "subscriptions/listen",
        {"notifications": {"resourceSubscriptions": ["boom://thing"]}},
        modern=True,
    )
    assert not response.streaming
    _assert_internal_error(response)
    _assert_logged(caplog, _VIEWSET_LOGGERS[True], RuntimeError)


@pytest.mark.parametrize("modern", [False, True], ids=["legacy", "modern"])
@pytest.mark.parametrize(
    ("tool", "exc_type"),
    [("boom.render", ValidationError), ("boom.runtime", RuntimeError)],
    ids=["api-exception", "runtime-error"],
)
async def test_a_streamed_dispatch_that_raises_ends_with_a_generic_error_frame(
    tool: str, exc_type: type, modern: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """Once the stream is open the status is spent, so the error rides as the last frame.

    It used to read ``ValidationError: [ErrorDetail(...)]``: the exception's
    own text, which is written for an operator rather than a client.
    """
    caplog.set_level(logging.ERROR, logger="rest_framework_mcp")
    response = await _post_async(
        _server(), "tools/call", {"name": tool, "arguments": {}}, modern=modern, progress=True
    )
    assert response.streaming
    frames = await _frames(response)
    assert frames[-1] == {
        "jsonrpc": "2.0",
        "id": REQUEST_ID,
        "error": {"code": -32603, "message": "Internal error"},
    }
    streamed = json.dumps(frames)
    assert "field is not found" not in streamed
    assert "db-primary" not in streamed
    _assert_logged(caplog, _STREAM_LOGGER, exc_type)


# ----- what the backstop must leave alone -----


@_PATHS
async def test_a_notification_is_never_dispatched_so_nothing_can_raise(
    is_async: bool, modern: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """A notification is acknowledged before dispatch, so its handler never runs.

    JSON-RPC forbids replying to one, and nothing here would: the ``202``
    comes back with no body and nothing is logged, even when the method
    named would raise if it were dispatched.
    """
    caplog.set_level(logging.ERROR, logger="rest_framework_mcp")
    response = await _post(
        is_async,
        _server(),
        "tools/call",
        {"name": "boom.runtime", "arguments": {}},
        modern=modern,
        notification=True,
    )
    assert response.status_code == 202
    assert response.content == b""
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_batch_is_refused_before_dispatch(is_async: bool) -> None:
    """The transport takes one message per POST; an array is ``-32600`` under ``400``."""
    body = json.dumps([{"jsonrpc": "2.0", "id": REQUEST_ID, "method": "ping"}])
    with override_settings(ROOT_URLCONF=urlconf_for(_server(), is_async=is_async)):
        if is_async:
            response = await AsyncClient().post("/mcp/", data=body, content_type="application/json")
        else:
            response = await sync_to_async(Client().post)(
                "/mcp/", data=body, content_type="application/json"
            )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


@_PATHS
async def test_a_permission_denial_keeps_its_403_and_challenge(
    is_async: bool, modern: bool
) -> None:
    """A denial is a value dispatch *returns*, so it never reaches the backstop."""
    response = await _post(
        is_async, _server(), "tools/call", {"name": "ok.gated", "arguments": {}}, modern=modern
    )
    assert response.status_code == 403, response.content
    assert response.json()["error"]["code"] == -32006
    assert "mcp:admin" in response["WWW-Authenticate"]


# Both flavours by name: the backstop catches them as one tuple, which is one
# branch arc to coverage, so dropping either would still read as 100%.
_RAISED_DENIALS = pytest.mark.parametrize("tool", ["deny.drf", "deny.django"])


def _assert_raised_denial(response: Any, caplog: pytest.LogCaptureFixture) -> None:
    """The answer a *returned* denial gets, and nothing of the exception's text."""
    assert response.status_code == 403, response.content
    assert response["Content-Type"].startswith("application/json")
    body = response.json()
    assert body["id"] == REQUEST_ID
    assert body["error"] == {"code": -32006, "message": "Insufficient permission"}
    assert "WWW-Authenticate" in response
    assert SECRET not in response.content.decode()
    # A refusal, so nothing lands at ERROR: that level is for server faults.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@_PATHS
@_RAISED_DENIALS
async def test_a_raised_permission_denial_is_a_denial_not_a_fault(
    is_async: bool, modern: bool, tool: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A permission class that raises instead of returning ``False``.

    DRF answered that with a ``403`` before the backstop existed; a catch-all
    ``-32603`` would report a refusal as a ``500``.
    """
    caplog.set_level(logging.ERROR, logger="rest_framework_mcp")
    response = await _post(
        is_async, _server(), "tools/call", {"name": tool, "arguments": {}}, modern=modern
    )
    _assert_raised_denial(response, caplog)


@pytest.mark.parametrize("modern", [False, True], ids=["legacy", "modern"])
@_RAISED_DENIALS
async def test_a_raised_denial_in_the_stream_preflight_is_a_403(
    modern: bool, tool: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The pre-flight runs the permission class before any stream opens."""
    caplog.set_level(logging.ERROR, logger="rest_framework_mcp")
    response = await _post_async(
        _server(),
        "tools/call",
        {"name": tool, "arguments": {}},
        modern=modern,
        progress=True,
    )
    assert not response.streaming
    _assert_raised_denial(response, caplog)


@_PATHS
async def test_a_rate_limit_keeps_its_error_and_retry_hint(is_async: bool, modern: bool) -> None:
    """Also returned, not raised: a ``200`` whose error carries ``retryAfter``."""
    response = await _post(
        is_async, _server(), "tools/call", {"name": "ok.limited", "arguments": {}}, modern=modern
    )
    assert response.status_code == 200, response.content
    error = response.json()["error"]
    assert error["code"] == -32005
    assert error["data"] == {"retryAfter": 42}


@_PATHS
async def test_an_unknown_method_keeps_its_own_answer(is_async: bool, modern: bool) -> None:
    """``-32601``, under the ``404`` the modern era makes normative."""
    response = await _post(is_async, _server(), "nope/nothing", {}, modern=modern)
    assert response.status_code == (404 if modern else 200), response.content
    assert response.json()["error"]["code"] == -32601


class _DenyingThrottle(BaseThrottle):
    def allow_request(self, request: Any, view: Any) -> bool:
        return False

    def wait(self) -> float:
        return 7


@pytest.mark.parametrize(
    "viewset", [StreamableHttpViewSet, AsyncStreamableHttpViewSet], ids=["sync", "async"]
)
async def test_a_drf_throttle_keeps_its_429_and_retry_after(
    viewset: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DRF's ``initial()`` runs before the action, so the backstop cannot see it.

    A project whose ``DEFAULT_THROTTLE_CLASSES`` reaches these viewsets keeps
    the ``429`` and ``Retry-After`` DRF renders for it. Patched on the class
    because DRF binds that setting to ``APIView`` at import.
    """
    monkeypatch.setattr(viewset, "throttle_classes", (_DenyingThrottle,))
    is_async = viewset is AsyncStreamableHttpViewSet
    body, headers = _request("tools/call", {"name": "ok.gated", "arguments": {}}, modern=True)
    with override_settings(ROOT_URLCONF=urlconf_for(_server(), is_async=is_async)):
        if is_async:
            response = await AsyncClient().post(
                "/mcp/", data=body, content_type="application/json", headers=headers
            )
        else:
            response = await sync_to_async(Client().post)(
                "/mcp/", data=body, content_type="application/json", headers=headers
            )
    assert response.status_code == 429, response.content
    assert response["Retry-After"] == "7"
