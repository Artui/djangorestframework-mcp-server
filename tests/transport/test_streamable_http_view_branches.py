from __future__ import annotations

import inspect
import json
from collections.abc import Generator
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import Client, RequestFactory

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from rest_framework_mcp.transport.streamable_http_viewset import (
    STREAMABLE_HTTP_ACTION_MAP,
    StreamableHttpViewSet,
    _reject_awaitable_token,
)


class _DenyAllBackend:
    """Auth backend that rejects every request — exercises the 401 path."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        return None

    def protected_resource_metadata(self) -> dict:
        return {}

    def www_authenticate_challenge(self, *, scopes=None, error=None) -> str:
        del scopes, error
        return 'Bearer realm="x", error="invalid_token"'


def test_unauthenticated_response_uses_backend_challenge() -> None:
    """When the auth backend returns ``None``, the transport emits 401 + WWW-Authenticate."""
    factory = RequestFactory()
    request = factory.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "x", "version": "1"},
                },
            }
        ),
        content_type="application/json",
    )

    view = StreamableHttpViewSet.as_view(
        STREAMABLE_HTTP_ACTION_MAP,
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        auth_backend=_DenyAllBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(),
    )
    response = view(request)
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")


def test_request_with_no_params_field(client: Client, initialized_session: str) -> None:
    """Omitting ``params`` entirely is valid — handler receives ``None``."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        HTTP_MCP_SESSION_ID=initialized_session,
    )
    body = response.json()
    assert body["result"] == {"resultType": "complete"}


class _AsyncAuthBackend(_DenyAllBackend):
    """Backend written async-native — correct only when mounted on ``async_urls``."""

    async def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        del request
        return TokenInfo(user=None, scopes=())


def _initialize_request() -> HttpRequest:
    return RequestFactory().post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "x", "version": "1"},
                },
            }
        ),
        content_type="application/json",
    )


def test_async_auth_backend_on_sync_transport_is_refused() -> None:
    """An ``async def authenticate`` under ``server.urls`` fails loudly, not open.

    Regression test for a documented configuration that authenticated every
    caller: the sync view cannot await, so the coroutine sailed through the
    ``token is None`` gate on its truthiness alone.
    """
    view = StreamableHttpViewSet.as_view(
        STREAMABLE_HTTP_ACTION_MAP,
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        auth_backend=_AsyncAuthBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(),
    )

    with pytest.raises(ImproperlyConfigured) as excinfo:
        view(_initialize_request())

    message = str(excinfo.value)
    assert "_AsyncAuthBackend.authenticate()" in message
    assert "server.async_urls" in message


def test_awaitable_token_is_closed_before_raising() -> None:
    """The coroutine is closed, so no "never awaited" warning buries the error."""

    async def _authenticate() -> None: ...

    coroutine = _authenticate()
    with pytest.raises(ImproperlyConfigured):
        _reject_awaitable_token(coroutine, backend=_AsyncAuthBackend())

    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


def test_non_coroutine_awaitable_token_is_refused() -> None:
    """A future-like return is refused too — there is nothing here that can await it."""

    class _Awaitable:
        def __await__(self) -> Generator[Any, None, None]:
            # Never driven: the transport refuses the object rather than
            # awaiting it. The method exists only to satisfy ``isawaitable``.
            yield

    with pytest.raises(ImproperlyConfigured):
        _reject_awaitable_token(_Awaitable(), backend=_AsyncAuthBackend())


def test_sync_token_passes_through_unchanged() -> None:
    """The ordinary sync return value is handed back untouched."""
    token = TokenInfo(user=None, scopes=())
    assert _reject_awaitable_token(token, backend=_DenyAllBackend()) is token


class _AllowAnyBackend(_DenyAllBackend):
    """Authenticates every caller — so the store, not auth, is under test."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        del request
        return TokenInfo(user=None, scopes=())


class _AsyncSessionStore(InMemorySessionStore):
    """A store written async-native — correct *only* under ``server.async_urls``.

    The likeliest instance of the awaitable-in-a-decision family, because
    unlike an async permission this one is a supported implementation: the
    async transport ``acall``s these methods. Mounting the same store on WSGI
    is the mistake, and nothing about the store itself is wrong.
    """

    async def create(self, *, principal_id: str) -> str:
        del principal_id
        return "s-1"  # pragma: no cover — never awaited, which is the defect

    async def owner(self, session_id: str) -> str | None:
        del session_id
        return None  # pragma: no cover — never awaited, which is the defect

    async def destroy(self, session_id: str) -> None:
        del session_id  # pragma: no cover — never awaited, which is the defect


def _view_with_store(store: Any) -> Any:
    return StreamableHttpViewSet.as_view(
        STREAMABLE_HTTP_ACTION_MAP,
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        auth_backend=_AllowAnyBackend(),
        session_store=store,
        config=build_mcp_config(),
    )


def test_async_session_store_create_on_sync_transport_is_refused() -> None:
    """``initialize`` refuses rather than minting a coroutine's repr as a session id."""
    with pytest.raises(ImproperlyConfigured) as excinfo:
        _view_with_store(_AsyncSessionStore())(_initialize_request())

    message = str(excinfo.value)
    assert "_AsyncSessionStore.create()" in message
    assert "server.async_urls" in message


def test_async_session_store_owner_on_sync_transport_is_refused() -> None:
    """The ownership gate refuses instead of answering "re-initialize" forever.

    This one fails *closed* without the guard — a coroutine matches no
    principal — but it fails closed with no legible cause, which is its own
    kind of outage.
    """
    request = RequestFactory().post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        HTTP_MCP_SESSION_ID="s-1",
    )

    with pytest.raises(ImproperlyConfigured) as excinfo:
        _view_with_store(_AsyncSessionStore())(request)

    assert "_AsyncSessionStore.owner()" in str(excinfo.value)


def test_async_session_store_destroy_on_sync_transport_is_refused() -> None:
    """DELETE is guarded too — ``destroy`` returns ``None``, so nothing else would notice.

    The only site in the family whose return value is *never* inspected: an
    un-awaited ``destroy`` coroutine would be discarded and the session would
    quietly survive its own termination. Guarding the call is the only thing
    that can see it.
    """

    class _AsyncDestroyOnly(InMemorySessionStore):
        async def destroy(self, session_id: str) -> None:
            del session_id  # pragma: no cover — never awaited, which is the defect

    store = _AsyncDestroyOnly()
    session_id = store.create(principal_id="anonymous")
    request = RequestFactory().delete("/mcp/", HTTP_MCP_SESSION_ID=session_id)

    with pytest.raises(ImproperlyConfigured) as excinfo:
        _view_with_store(store)(request)

    assert "_AsyncDestroyOnly.destroy()" in str(excinfo.value)


def test_response_shaped_input_is_rejected(client: Client, initialized_session: str) -> None:
    """Posting a JSON-RPC response (vs request/notification) returns -32600."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        HTTP_MCP_SESSION_ID=initialized_session,
    )
    body = response.json()
    assert body["error"]["code"] == -32600
