from __future__ import annotations

import json

from django.http import HttpRequest
from django.test import Client, RequestFactory

from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from rest_framework_mcp.transport.streamable_http_view import StreamableHttpView


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

    view = StreamableHttpView.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        auth_backend=_DenyAllBackend(),
        session_store=InMemorySessionStore(),
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
    assert body["result"] == {}


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
