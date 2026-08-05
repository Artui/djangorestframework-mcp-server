"""``SESSIONS_ENABLED=False`` — the legacy era served without session state.

A conformant mode, not a relaxation: both legacy revisions say a server **MAY**
assign a session ID at initialization, and make the client's duty to echo one
conditional on it having arrived. A server that never assigns is never sent one.

Why it exists: a session is state, and state expires, gets evicted, and dies
with a deploy. Every one of those reaches the client as a ``404`` whose remedy
is to re-``initialize`` — which not every client does, turning a recoverable
condition into an outage. This removes the failure class server-side, for every
client, without waiting on one to implement the remedy.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
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
_SESSION_HEADER = "Mcp-Session-Id"


class _HeaderPrincipalBackend:
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


def _view(*, sessions: bool, is_async: bool = False, broker: Any = None) -> Any:
    common: dict[str, Any] = {
        "tools": ToolRegistry(),
        "resources": ResourceRegistry(),
        "prompts": PromptRegistry(),
        "auth_backend": _HeaderPrincipalBackend(),
        "session_store": InMemorySessionStore(),
        "config": build_mcp_config(sessions_enabled=sessions),
    }
    if is_async:
        return AsyncStreamableHttpViewSet.as_view(
            ASYNC_STREAMABLE_HTTP_ACTION_MAP, sse_broker=broker, **common
        )
    return StreamableHttpViewSet.as_view(STREAMABLE_HTTP_ACTION_MAP, **common)


def _post_request(method: str = "ping", *, session_id: str | None = None) -> Any:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if method == "initialize":
        payload["params"] = {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "x", "version": "0"},
        }
    headers = {"Mcp-Protocol-Version": "2025-11-25", "X-Principal": "alice"}
    if session_id is not None:
        headers[_SESSION_HEADER] = session_id
    return factory.post(
        "/mcp/", data=json.dumps(payload), content_type="application/json", headers=headers
    )


# ---------- initialize: assignment is the server's choice ----------


def test_initialize_mints_no_session_id_when_disabled() -> None:
    response = _view(sessions=False)(_post_request("initialize"))
    assert response.status_code == 200
    assert _SESSION_HEADER not in response


def test_initialize_still_mints_by_default() -> None:
    """Default is unchanged — this is opt-out, not a behaviour change."""
    response = _view(sessions=True)(_post_request("initialize"))
    assert response.status_code == 200
    assert response[_SESSION_HEADER]


# ---------- POST: the gate the incident tripped over ----------


def test_a_call_with_no_session_id_succeeds_when_disabled() -> None:
    """The whole point: no id, no expiry, no eviction, no post-deploy outage."""
    response = _view(sessions=False)(_post_request("ping"))
    assert response.status_code == 200


def test_a_stale_session_id_is_ignored_rather_than_rejected() -> None:
    """A client still echoing an id from before the switch keeps working.

    Without this, turning sessions off would itself cause the outage it exists
    to prevent — every client holding an id would 404 until it re-initialized.
    """
    response = _view(sessions=False)(_post_request("ping", session_id="from-a-past-life"))
    assert response.status_code == 200


async def test_the_async_path_agrees() -> None:
    response = await _view(sessions=False, is_async=True)(_post_request("ping"))
    assert response.status_code == 200


# ---------- GET / DELETE: 405, the status the spec defines ----------


async def test_sse_get_is_405_when_disabled() -> None:
    """No session id means no channel address, so there is no stream to open."""
    request = factory.get(
        "/mcp/", headers={"X-Principal": "alice", "Mcp-Protocol-Version": "2025-11-25"}
    )
    response = await _view(sessions=False, is_async=True, broker=InMemorySSEBroker())(request)
    assert response.status_code == 405


@pytest.mark.parametrize("is_async", [False, True])
async def test_delete_is_405_when_disabled(is_async: bool) -> None:
    """ "The server MAY respond … with 405 … does not allow clients to terminate
    sessions" — true by construction when there are none."""
    request = factory.delete("/mcp/", headers={"X-Principal": "alice"})
    view = _view(sessions=False, is_async=is_async)
    response = await view(request) if is_async else view(request)
    assert response.status_code == 405


# ---------- the modern era is unaffected ----------


async def test_modern_era_is_sessionless_regardless_of_the_setting() -> None:
    """It never touched a session, so the setting must not change it either way."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "ping",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "x", "version": "0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }
    for sessions in (True, False):
        request = factory.post(
            "/mcp/",
            data=json.dumps(payload),
            content_type="application/json",
            headers={
                "X-Principal": "alice",
                "Mcp-Protocol-Version": "2026-07-28",
                # The modern era mirrors the method in a header for gateway
                # routing; ``validate_modern_request`` requires it to agree.
                "Mcp-Method": "ping",
            },
        )
        response = await _view(sessions=sessions, is_async=True)(request)
        assert response.status_code == 200, (sessions, response.content)
