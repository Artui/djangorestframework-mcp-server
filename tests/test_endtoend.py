from __future__ import annotations

import json

import pytest
from django.test import Client


def test_initialize_returns_session_id_and_capabilities(jsonrpc) -> None:
    response = jsonrpc(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.0"},
        },
        protocol_version=None,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert "result" in body
    assert body["result"]["protocolVersion"] == "2025-11-25"
    assert body["result"]["capabilities"]["tools"] == {}
    assert body["result"]["capabilities"]["resources"] == {}
    assert response["Mcp-Session-Id"]


def test_tools_list_returns_create_tool(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc("tools/list", {}, session_id=initialized_session)
    assert response.status_code == 200
    body = response.json()
    names = [t["name"] for t in body["result"]["tools"]]
    assert "invoices.create" in names
    create_tool = next(t for t in body["result"]["tools"] if t["name"] == "invoices.create")
    assert create_tool["inputSchema"]["type"] == "object"
    assert "number" in create_tool["inputSchema"]["properties"]
    assert "amount_cents" in create_tool["inputSchema"]["properties"]


def test_resources_templates_list_includes_retrieve(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc("resources/templates/list", {}, session_id=initialized_session)
    assert response.status_code == 200
    templates = response.json()["result"]["resourceTemplates"]
    uris = [t["uriTemplate"] for t in templates]
    assert "invoices://{pk}" in uris


@pytest.mark.django_db
def test_tools_call_creates_invoice(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {"name": "invoices.create", "arguments": {"number": "INV-1", "amount_cents": 100}},
        session_id=initialized_session,
    )
    assert response.status_code == 200
    body = response.json()
    assert "result" in body, body
    structured = body["result"]["structuredContent"]
    assert structured["number"] == "INV-1"
    assert structured["amount_cents"] == 100
    assert structured["sent"] is False
    # The text block is JSON-formatted by default.
    text_block = body["result"]["content"][0]
    assert text_block["type"] == "text"
    assert json.loads(text_block["text"])["number"] == "INV-1"


@pytest.mark.django_db
def test_tools_call_returns_invalid_params_on_missing_field(
    jsonrpc, initialized_session: str
) -> None:
    response = jsonrpc(
        "tools/call",
        {"name": "invoices.create", "arguments": {"amount_cents": 5}},
        session_id=initialized_session,
    )
    assert response.status_code == 200
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == -32602
    assert "number" in body["error"]["data"]["detail"]


def test_unknown_tool_returns_tool_not_found(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {"name": "does.not.exist", "arguments": {}},
        session_id=initialized_session,
    )
    body = response.json()
    assert body["error"]["code"] == -32004


def test_call_without_session_id_returns_404(jsonrpc) -> None:
    response = jsonrpc("tools/list", {})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == -32600


def test_call_without_protocol_version_returns_400(client: Client) -> None:
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
    )
    assert response.status_code == 400


def test_unknown_method_returns_method_not_found(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc("not/a/real/method", {}, session_id=initialized_session)
    body = response.json()
    assert body["error"]["code"] == -32601


def test_delete_terminates_session(client: Client, initialized_session: str) -> None:
    response = client.delete("/mcp/", HTTP_MCP_SESSION_ID=initialized_session)
    assert response.status_code == 204
    # Subsequent calls with that session id should now fail.
    follow_up = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
        HTTP_MCP_SESSION_ID=initialized_session,
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    assert follow_up.status_code == 404


def test_get_returns_405(client: Client) -> None:
    response = client.get("/mcp/")
    assert response.status_code == 405


def test_protected_resource_metadata(client: Client) -> None:
    response = client.get("/mcp/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    body = response.json()
    assert "resource" in body
    assert "bearer_methods_supported" in body


@pytest.mark.django_db
def test_resources_read_invokes_selector(client: Client, jsonrpc, initialized_session: str) -> None:
    # Create one invoice via the tool, then read it back through the retrieve resource.
    create = jsonrpc(
        "tools/call",
        {"name": "invoices.create", "arguments": {"number": "INV-2", "amount_cents": 50}},
        session_id=initialized_session,
    )
    invoice_id = create.json()["result"]["structuredContent"]["id"]

    read = jsonrpc(
        "resources/read",
        {"uri": f"invoices://{invoice_id}"},
        session_id=initialized_session,
    )
    body = read.json()
    contents = body["result"]["contents"]
    assert len(contents) == 1
    assert contents[0]["uri"] == f"invoices://{invoice_id}"
    assert contents[0]["mimeType"] == "application/json"
    assert json.loads(contents[0]["text"])["number"] == "INV-2"


def test_notification_returns_202(client: Client, initialized_session: str) -> None:
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        content_type="application/json",
        HTTP_MCP_SESSION_ID=initialized_session,
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    assert response.status_code == 202
