from __future__ import annotations

import json

from django.test import Client


def test_post_with_too_large_body(client: Client, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["*"],
        "MAX_REQUEST_BYTES": 10,
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    big_body: bytes = b"X" * 1024
    response = client.post("/mcp/", data=big_body, content_type="application/json")
    assert response.status_code == 413


def test_post_with_invalid_json(client: Client) -> None:
    response = client.post(
        "/mcp/",
        data="not json",
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    body = response.json()
    assert body["error"]["code"] == -32700


def test_post_with_invalid_jsonrpc_shape(client: Client) -> None:
    response = client.post(
        "/mcp/",
        data=json.dumps({"foo": "bar"}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    body = response.json()
    assert body["error"]["code"] == -32600


def test_origin_not_allowed_returns_403(client: Client, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["https://allowed.example"],
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        content_type="application/json",
        HTTP_ORIGIN="https://blocked.example",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    assert response.status_code == 403


def test_get_blocked_origin_returns_403(client: Client, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["https://allowed.example"],
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    response = client.get("/mcp/", HTTP_ORIGIN="https://blocked.example")
    assert response.status_code == 403


def test_delete_without_session_id_is_204(client: Client) -> None:
    response = client.delete("/mcp/")
    assert response.status_code == 204


def test_delete_blocked_origin_returns_403(client: Client, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["https://allowed.example"],
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    response = client.delete("/mcp/", HTTP_ORIGIN="https://blocked.example")
    assert response.status_code == 403


def test_post_response_jsonrpc_id_with_jsonrpc_request_only(client: Client) -> None:
    """A bare JSON-RPC response object posted to the server is rejected."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    body = response.json()
    assert body["error"]["code"] == -32600


def test_post_with_list_params_treated_as_no_params(
    client: Client, initialized_session: str
) -> None:
    """JSON-RPC list-shaped params are silently coerced to None for MCP methods."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1, 2]}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        HTTP_MCP_SESSION_ID=initialized_session,
    )
    body = response.json()
    assert "result" in body


def test_initialize_with_unsupported_protocol_header_falls_back(client: Client, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["*"],
        "PROTOCOL_VERSIONS": ["2025-11-25"],
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    response = client.post(
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
        HTTP_MCP_PROTOCOL_VERSION="9999-99-99",
    )
    # Even with a bogus header on initialize, the server falls back to its
    # configured default — initialize is the one method allowed without a
    # known protocol-version header.
    assert response.status_code == 200
