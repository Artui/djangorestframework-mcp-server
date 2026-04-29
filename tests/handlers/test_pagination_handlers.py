"""Pagination wired through tools/list, resources/list, resources/templates/list."""

from __future__ import annotations

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_resources_list import handle_resources_list
from rest_framework_mcp.handlers.handle_resources_templates_list import (
    handle_resources_templates_list,
)
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _build_server() -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    for i in range(5):
        server.register_service_tool(
            name=f"tool.{i}", spec=ServiceSpec(service=lambda: None, atomic=False)
        )
    for i in range(4):
        server.register_resource(
            name=f"r.{i}",
            uri_template=f"r{i}://",
            selector=SelectorSpec(selector=lambda: None),
        )
    for i in range(3):
        server.register_resource(
            name=f"rt.{i}",
            uri_template=f"rt{i}://" + "{pk}",
            selector=SelectorSpec(selector=lambda *, pk: None),
        )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    from django.http import HttpRequest

    from rest_framework_mcp.auth.token_info import TokenInfo

    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def test_tools_list_paginates(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PAGE_SIZE": 2}
    server = _build_server()
    out1 = handle_tools_list(None, _ctx(server))
    assert isinstance(out1, dict)
    assert len(out1["tools"]) == 2
    assert "nextCursor" in out1
    out2 = handle_tools_list({"cursor": out1["nextCursor"]}, _ctx(server))
    assert isinstance(out2, dict)
    assert len(out2["tools"]) == 2
    assert "nextCursor" in out2
    out3 = handle_tools_list({"cursor": out2["nextCursor"]}, _ctx(server))
    assert isinstance(out3, dict)
    assert len(out3["tools"]) == 1
    assert "nextCursor" not in out3


def test_resources_list_paginates(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PAGE_SIZE": 2}
    server = _build_server()
    out = handle_resources_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert len(out["resources"]) == 2
    assert "nextCursor" in out


def test_resources_templates_list_paginates(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PAGE_SIZE": 2}
    server = _build_server()
    out = handle_resources_templates_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert len(out["resourceTemplates"]) == 2
    assert "nextCursor" in out


def test_tools_list_rejects_non_string_cursor() -> None:
    server = _build_server()
    out = handle_tools_list({"cursor": 42}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


def test_resources_list_rejects_non_string_cursor() -> None:
    server = _build_server()
    out = handle_resources_list({"cursor": 42}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_resources_templates_list_rejects_non_string_cursor() -> None:
    server = _build_server()
    out = handle_resources_templates_list({"cursor": 42}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_tools_list_rejects_malformed_cursor() -> None:
    server = _build_server()
    out = handle_tools_list({"cursor": "###"}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_resources_list_rejects_malformed_cursor() -> None:
    server = _build_server()
    out = handle_resources_list({"cursor": "###"}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_resources_templates_list_rejects_malformed_cursor() -> None:
    server = _build_server()
    out = handle_resources_templates_list({"cursor": "###"}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602
