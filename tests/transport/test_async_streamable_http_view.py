from __future__ import annotations

import json

import pytest
from django.http import HttpRequest
from django.test import AsyncClient, RequestFactory, override_settings

from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.async_streamable_http_view import AsyncStreamableHttpView
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


@pytest.fixture
def async_urlconf():
    """Switch the test app to its async URL conf for the duration of one test."""
    with override_settings(ROOT_URLCONF="tests.testapp.async_urls"):
        yield


async def _initialize(client: AsyncClient) -> str:
    response = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0.0"},
                },
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    return response["Mcp-Session-Id"]


async def test_async_initialize_returns_session(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    assert sid


async def test_async_tools_list(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    body = response.json()
    names = [t["name"] for t in body["result"]["tools"]]
    assert "invoices.create" in names


@pytest.mark.django_db(transaction=True)
async def test_async_tools_call_creates_invoice(async_urlconf) -> None:
    """The async dispatch path runs ``arun_service`` end-to-end against a real DB."""
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "invoices.create",
                    "arguments": {"number": "ASYNC-1", "amount_cents": 250},
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    body = response.json()
    assert "result" in body, body
    assert body["result"]["structuredContent"]["number"] == "ASYNC-1"


@pytest.mark.django_db(transaction=True)
async def test_async_resources_read(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    create = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "invoices.create",
                    "arguments": {"number": "ASYNC-2", "amount_cents": 500},
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    invoice_id = create.json()["result"]["structuredContent"]["id"]

    read = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "resources/read",
                "params": {"uri": f"invoices://{invoice_id}"},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    text = read.json()["result"]["contents"][0]["text"]
    assert json.loads(text)["number"] == "ASYNC-2"


async def test_async_unknown_session_returns_404(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 6, "method": "tools/list"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": "not-real"},
    )
    assert response.status_code == 404


async def test_async_get_without_broker_returns_405() -> None:
    """When no broker is wired, GET is 405 (spec-compliant fallback)."""
    factory = RequestFactory()
    request = factory.get("/mcp/")
    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend

    view = AsyncStreamableHttpView.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        sse_broker=None,
    )
    response = await view(request)
    assert response.status_code == 405


async def test_async_get_without_session_id_returns_404(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.get("/mcp/", headers={"Mcp-Protocol-Version": "2025-11-25"})
    assert response.status_code == 404


async def test_async_get_unknown_session_returns_404(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.get(
        "/mcp/",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": "not-a-session"},
    )
    assert response.status_code == 404


async def test_async_get_missing_protocol_version_returns_400(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.get("/mcp/")
    assert response.status_code == 400


async def test_async_delete_terminates_session(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.delete("/mcp/", headers={"Mcp-Session-Id": sid})
    assert response.status_code == 204
    follow_up = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    assert follow_up.status_code == 404


async def test_async_invalid_json(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.post(
        "/mcp/",
        data="not json",
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25"},
    )
    assert response.json()["error"]["code"] == -32700


async def test_async_invalid_request_shape(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.post(
        "/mcp/",
        data=json.dumps({"foo": "bar"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25"},
    )
    assert response.json()["error"]["code"] == -32600


async def test_async_missing_protocol_version(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/list"}),
        content_type="application/json",
    )
    assert response.status_code == 400


async def test_async_body_too_large(async_urlconf, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["*"],
        "MAX_REQUEST_BYTES": 10,
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    client = AsyncClient()
    response = await client.post("/mcp/", data=b"X" * 256, content_type="application/json")
    assert response.status_code == 413


async def test_async_blocked_origin_post(async_urlconf, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["https://allowed.example"],
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    client = AsyncClient()
    response = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}),
        content_type="application/json",
        headers={
            "Origin": "https://blocked.example",
            "Mcp-Protocol-Version": "2025-11-25",
        },
    )
    assert response.status_code == 403


async def test_async_blocked_origin_get(async_urlconf, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["https://allowed.example"],
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    client = AsyncClient()
    response = await client.get("/mcp/", headers={"Origin": "https://blocked.example"})
    assert response.status_code == 403


async def test_async_blocked_origin_delete(async_urlconf, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["https://allowed.example"],
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    client = AsyncClient()
    response = await client.delete("/mcp/", headers={"Origin": "https://blocked.example"})
    assert response.status_code == 403


async def test_async_unauthenticated_returns_401() -> None:
    """Auth backend that returns ``None`` produces a 401 with WWW-Authenticate."""

    class _Deny:
        def authenticate(self, request: HttpRequest) -> TokenInfo | None:
            return None

        def protected_resource_metadata(self) -> dict:
            return {}

        def www_authenticate_challenge(self, *, scopes=None, error=None) -> str:
            del scopes, error
            return 'Bearer realm="x", error="invalid_token"'

    factory = RequestFactory()
    request = factory.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 10,
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
    view = AsyncStreamableHttpView.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        auth_backend=_Deny(),
        session_store=InMemorySessionStore(),
    )
    response = await view(request)
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")


async def test_async_notification_returns_202(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    assert response.status_code == 202


async def test_async_response_shaped_input_rejected(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 11, "result": {"ok": True}}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    assert response.json()["error"]["code"] == -32600


async def test_async_list_params_treated_as_no_params(async_urlconf) -> None:
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 12, "method": "tools/list", "params": [1, 2]}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    body = response.json()
    assert "result" in body


async def test_async_initialize_with_unsupported_version_falls_back(
    async_urlconf, settings
) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["*"],
        "PROTOCOL_VERSIONS": ["2025-11-25"],
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    client = AsyncClient()
    response = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "x", "version": "1"},
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "9999-99-99"},
    )
    assert response.status_code == 200


async def test_async_dispatch_error_envelope(async_urlconf) -> None:
    """A handler returning a ``JsonRpcError`` is wrapped in an ``error`` envelope."""
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {"name": "does.not.exist", "arguments": {}},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    body = response.json()
    assert body["error"]["code"] == -32004


async def test_async_delete_without_session_id_is_204(async_urlconf) -> None:
    client = AsyncClient()
    response = await client.delete("/mcp/")
    assert response.status_code == 204


async def test_async_request_with_no_params_field(async_urlconf) -> None:
    """Omitting ``params`` entirely is valid — handler receives ``None``."""
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 15, "method": "ping"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    assert response.json()["result"] == {}


async def test_acall_invokes_async_function_directly() -> None:
    """``acall`` skips the thread hop when the callable is already async."""
    from rest_framework_mcp._compat.acall import acall

    async def aproduce(x: int) -> int:
        return x * 2

    assert await acall(aproduce, 21) == 42


async def test_acall_invokes_sync_function_via_thread() -> None:
    from rest_framework_mcp._compat.acall import acall

    def produce(x: int) -> int:
        return x * 2

    assert await acall(produce, 21) == 42
