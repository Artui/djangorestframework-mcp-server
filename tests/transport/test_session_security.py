"""Wire-conformance tests for transport auth ordering + session binding.

Covers the 0.7.0 security hardening:

- POST authenticates *before* the session lookup (no 404-vs-401 oracle).
- GET (SSE) and DELETE require authentication on both viewsets.
- Sessions are bound to the principal that initialized them; another
  principal presenting the id sees the same 404 as an unknown session.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from django.http import HttpRequest
from django.test import RequestFactory

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.async_streamable_http_viewset import (
    ASYNC_STREAMABLE_HTTP_ACTION_MAP,
    AsyncStreamableHttpViewSet,
)
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from rest_framework_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from rest_framework_mcp.transport.streamable_http_viewset import (
    STREAMABLE_HTTP_ACTION_MAP,
    StreamableHttpViewSet,
)

factory = RequestFactory()


class _HeaderPrincipalBackend:
    """Authenticates via the ``X-Principal`` header — distinct test principals."""

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        principal: str | None = request.headers.get("X-Principal")
        if principal is None:
            return None
        return TokenInfo(user=SimpleNamespace(pk=principal))

    def protected_resource_metadata(self) -> dict:
        return {}

    def www_authenticate_challenge(self, *, scopes: Any = None, error: Any = None) -> str:
        del scopes, error
        return 'Bearer realm="x", error="invalid_token"'


def _sync_view(store: InMemorySessionStore) -> Any:
    return StreamableHttpViewSet.as_view(
        STREAMABLE_HTTP_ACTION_MAP,
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        auth_backend=_HeaderPrincipalBackend(),
        session_store=store,
    )


def _async_view(store: InMemorySessionStore, *, broker: InMemorySSEBroker | None = None) -> Any:
    return AsyncStreamableHttpViewSet.as_view(
        ASYNC_STREAMABLE_HTTP_ACTION_MAP,
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        auth_backend=_HeaderPrincipalBackend(),
        session_store=store,
        sse_broker=broker,
    )


def _post(
    view: Any,
    *,
    method: str = "ping",
    session_id: str | None = None,
    principal: str | None = None,
    request_id: int = 1,
) -> Any:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if method == "initialize":
        payload["params"] = {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "x", "version": "0"},
        }
    headers: dict[str, str] = {"Mcp-Protocol-Version": "2025-11-25"}
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    if principal is not None:
        headers["X-Principal"] = principal
    request = factory.post(
        "/mcp/", data=json.dumps(payload), content_type="application/json", headers=headers
    )
    return view(request)


# ---------- POST: auth before session lookup (SEC-2) ----------


def test_unauthenticated_post_with_bogus_session_is_401_not_404() -> None:
    """Session validity must not be probeable without a credential."""
    view = _sync_view(InMemorySessionStore())
    response = _post(view, session_id="made-up-id")
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")


def test_authenticated_post_with_bogus_session_is_404() -> None:
    view = _sync_view(InMemorySessionStore())
    response = _post(view, session_id="made-up-id", principal="alice")
    assert response.status_code == 404


# ---------- POST: session bound to principal (SEC-3) ----------


def test_session_is_bound_to_initializing_principal() -> None:
    store = InMemorySessionStore()
    view = _sync_view(store)
    init = _post(view, method="initialize", principal="alice")
    assert init.status_code == 200
    sid = init["Mcp-Session-Id"]
    assert store.owner(sid) == "user:alice"

    same = _post(view, session_id=sid, principal="alice", request_id=2)
    assert same.status_code == 200

    other = _post(view, session_id=sid, principal="mallory", request_id=3)
    assert other.status_code == 404


# ---------- GET (SEC-1) ----------


def test_sync_get_requires_authentication() -> None:
    view = _sync_view(InMemorySessionStore())
    response = view(factory.get("/mcp/"))
    assert response.status_code == 401


def test_sync_get_authenticated_is_405() -> None:
    view = _sync_view(InMemorySessionStore())
    response = view(factory.get("/mcp/", headers={"X-Principal": "alice"}))
    assert response.status_code == 405


async def test_async_get_requires_authentication() -> None:
    view = _async_view(InMemorySessionStore(), broker=InMemorySSEBroker())
    response = await view(factory.get("/mcp/"))
    assert response.status_code == 401


async def test_async_get_wrong_principal_is_404() -> None:
    store = InMemorySessionStore()
    sid = store.create(principal_id="user:alice")
    view = _async_view(store, broker=InMemorySSEBroker())
    response = await view(
        factory.get(
            "/mcp/",
            headers={
                "X-Principal": "mallory",
                "Mcp-Protocol-Version": "2025-11-25",
                "Mcp-Session-Id": sid,
            },
        )
    )
    assert response.status_code == 404


# ---------- DELETE (SEC-1 + SEC-3) ----------


def test_sync_delete_requires_authentication() -> None:
    view = _sync_view(InMemorySessionStore())
    response = view(factory.delete("/mcp/"))
    assert response.status_code == 401


def test_sync_delete_wrong_principal_is_404_and_session_survives() -> None:
    store = InMemorySessionStore()
    sid = store.create(principal_id="user:alice")
    view = _sync_view(store)
    response = view(
        factory.delete("/mcp/", headers={"X-Principal": "mallory", "Mcp-Session-Id": sid})
    )
    assert response.status_code == 404
    assert store.exists(sid)


def test_sync_delete_owner_destroys_session() -> None:
    store = InMemorySessionStore()
    sid = store.create(principal_id="user:alice")
    view = _sync_view(store)
    response = view(
        factory.delete("/mcp/", headers={"X-Principal": "alice", "Mcp-Session-Id": sid})
    )
    assert response.status_code == 204
    assert not store.exists(sid)


def test_sync_delete_without_session_id_is_a_noop_204() -> None:
    view = _sync_view(InMemorySessionStore())
    response = view(factory.delete("/mcp/", headers={"X-Principal": "alice"}))
    assert response.status_code == 204


async def test_async_delete_requires_authentication() -> None:
    view = _async_view(InMemorySessionStore())
    response = await view(factory.delete("/mcp/"))
    assert response.status_code == 401


async def test_async_delete_wrong_principal_is_404_and_session_survives() -> None:
    store = InMemorySessionStore()
    sid = store.create(principal_id="user:alice")
    view = _async_view(store)
    response = await view(
        factory.delete("/mcp/", headers={"X-Principal": "mallory", "Mcp-Session-Id": sid})
    )
    assert response.status_code == 404
    assert store.exists(sid)


async def test_async_delete_owner_destroys_session() -> None:
    store = InMemorySessionStore()
    sid = store.create(principal_id="user:alice")
    view = _async_view(store)
    response = await view(
        factory.delete("/mcp/", headers={"X-Principal": "alice", "Mcp-Session-Id": sid})
    )
    assert response.status_code == 204
    assert not store.exists(sid)
