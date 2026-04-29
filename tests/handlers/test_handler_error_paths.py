from __future__ import annotations

import pytest
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_initialize import handle_initialize
from rest_framework_mcp.handlers.handle_ping import handle_ping
from rest_framework_mcp.handlers.handle_resources_list import handle_resources_list
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError


def _ctx_from_test_app() -> MCPCallContext:
    from django.http import HttpRequest

    from rest_framework_mcp.auth.token_info import TokenInfo
    from tests.testapp.urls import server

    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def test_handle_initialize_rejects_non_dict_params() -> None:
    ctx = _ctx_from_test_app()
    out = handle_initialize(None, ctx)
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


def test_handle_initialize_negotiates_unknown_version() -> None:
    ctx = _ctx_from_test_app()
    out = handle_initialize(
        {
            "protocolVersion": "1999-01-01",
            "capabilities": {},
            "clientInfo": {"name": "x", "version": "1"},
        },
        ctx,
    )
    # Unsupported version → server falls back to its preferred version.
    assert not isinstance(out, JsonRpcError)
    assert out.protocol_version == "2025-11-25"


def test_handle_ping_returns_empty_object() -> None:
    out = handle_ping(None, _ctx_from_test_app())
    assert out == {}


def test_handle_tools_call_rejects_non_dict_params() -> None:
    out = handle_tools_call(None, _ctx_from_test_app())
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


def test_handle_tools_call_rejects_missing_name() -> None:
    out = handle_tools_call({}, _ctx_from_test_app())
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


def test_handle_tools_call_rejects_non_dict_arguments() -> None:
    """A truthy non-dict ``arguments`` value (e.g. a populated list) is rejected.

    A falsy non-dict (``[]``, ``None``, ``""``) is normalised to ``{}`` because
    ``params.get("arguments") or {}`` short-circuits, so we use a populated
    list to exercise the explicit dict-shape guard.
    """
    out = handle_tools_call(
        {"name": "invoices.create", "arguments": [1, 2, 3]}, _ctx_from_test_app()
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


def test_handle_resources_read_rejects_non_dict_params() -> None:
    out = handle_resources_read(None, _ctx_from_test_app())
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


def test_handle_resources_read_rejects_missing_uri() -> None:
    out = handle_resources_read({}, _ctx_from_test_app())
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


def test_handle_resources_read_unknown_uri() -> None:
    out = handle_resources_read({"uri": "nope://x"}, _ctx_from_test_app())
    assert isinstance(out, JsonRpcError)
    assert out.code == -32003


def test_handle_resources_list_returns_concrete() -> None:
    out = handle_resources_list({}, _ctx_from_test_app())
    assert isinstance(out, dict)
    uris = [r["uri"] for r in out["resources"]]
    assert "invoices://" in uris


@pytest.mark.django_db
def test_handle_tools_call_translates_service_error(jsonrpc, initialized_session: str) -> None:
    """A service that raises ``ServiceError`` returns a -32000 JSON-RPC error.

    Wires a one-shot tool through the running server so we exercise the full
    error mapping path inside the transport.
    """
    from tests.testapp.urls import server

    def boom() -> None:
        raise ServiceError("nope")

    server.register_service_tool(name="boom.raise", spec=ServiceSpec(service=boom))
    try:
        response = jsonrpc(
            "tools/call",
            {"name": "boom.raise", "arguments": {}},
            session_id=initialized_session,
        )
        body = response.json()
        assert body["error"]["code"] == -32000
    finally:
        # Best-effort cleanup so other tests don't see the duplicate name.
        # (ToolRegistry has no public unregister; the test ordering tolerates this
        #  because each test gets a fresh client but the URL conf is cached.)
        server.tools._bindings.pop("boom.raise", None)  # type: ignore[attr-defined]
