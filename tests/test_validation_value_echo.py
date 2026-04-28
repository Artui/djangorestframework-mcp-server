"""Coverage for ``REST_FRAMEWORK_MCP['INCLUDE_VALIDATION_VALUE']``.

The setting is opt-in: when True, ``data.value`` echoes the offending
``arguments`` dict back to the client. We exercise both states across all
four handler call sites — sync + async, tools/call DRF + ServiceValidationError,
and prompts/get missing-required-args.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, PromptArgument
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_prompts_get import handle_prompts_get
from rest_framework_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_binding import ToolBinding
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


@dataclass
class _Args:
    name: str


def _ctx(tools: ToolRegistry) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=tools,
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


def _tools_with_dataclass_input() -> ToolRegistry:
    def svc(*, data: _Args) -> dict[str, Any]:
        return {"name": data.name}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, input_serializer=_Args, atomic=False),
        )
    )
    return tools


def _tools_raising_validation() -> ToolRegistry:
    def svc() -> None:
        raise ServiceValidationError({"f": ["bad"]})

    tools = ToolRegistry()
    tools.register(
        ToolBinding(name="t", description=None, spec=ServiceSpec(service=svc, atomic=False))
    )
    return tools


# ---------- tools/call DRF ValidationError ----------


def test_sync_tools_call_drf_validation_default_omits_value(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    out = handle_tools_call(
        {"name": "t", "arguments": {"junk": True}}, _ctx(_tools_with_dataclass_input())
    )
    assert isinstance(out, JsonRpcError)
    assert "value" not in out.data


def test_sync_tools_call_drf_validation_echo(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_VALIDATION_VALUE": True}
    out = handle_tools_call(
        {"name": "t", "arguments": {"junk": True}}, _ctx(_tools_with_dataclass_input())
    )
    assert isinstance(out, JsonRpcError)
    assert out.data["value"] == {"junk": True}


async def test_async_tools_call_drf_validation_default_omits_value(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    out = await handle_tools_call_async(
        {"name": "t", "arguments": {"junk": True}}, _ctx(_tools_with_dataclass_input())
    )
    assert isinstance(out, JsonRpcError)
    assert "value" not in out.data


async def test_async_tools_call_drf_validation_echo(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_VALIDATION_VALUE": True}
    out = await handle_tools_call_async(
        {"name": "t", "arguments": {"junk": True}}, _ctx(_tools_with_dataclass_input())
    )
    assert isinstance(out, JsonRpcError)
    assert out.data["value"] == {"junk": True}


# ---------- tools/call ServiceValidationError ----------


def test_sync_tools_call_service_validation_default_omits_value(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(_tools_raising_validation()))
    assert isinstance(out, JsonRpcError)
    assert "value" not in out.data


def test_sync_tools_call_service_validation_echo(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_VALIDATION_VALUE": True}
    out = handle_tools_call({"name": "t", "arguments": {"x": 1}}, _ctx(_tools_raising_validation()))
    assert isinstance(out, JsonRpcError)
    assert out.data["value"] == {"x": 1}


async def test_async_tools_call_service_validation_default_omits_value(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    out = await handle_tools_call_async(
        {"name": "t", "arguments": {}}, _ctx(_tools_raising_validation())
    )
    assert isinstance(out, JsonRpcError)
    assert "value" not in out.data


async def test_async_tools_call_service_validation_echo(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_VALIDATION_VALUE": True}
    out = await handle_tools_call_async(
        {"name": "t", "arguments": {"x": 1}}, _ctx(_tools_raising_validation())
    )
    assert isinstance(out, JsonRpcError)
    assert out.data["value"] == {"x": 1}


# ---------- prompts/get missing required args ----------


def _server_with_required_prompt() -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_prompt(
        name="echo",
        render=lambda *, who: f"hi {who}",
        arguments=[PromptArgument(name="who", required=True)],
    )
    return server


def _ctx_for(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def test_sync_prompts_get_missing_default_omits_value(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    server = _server_with_required_prompt()
    out = handle_prompts_get({"name": "echo", "arguments": {}}, _ctx_for(server))
    assert isinstance(out, JsonRpcError)
    assert out.data == {"missing": ["who"]}


def test_sync_prompts_get_missing_echo(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_VALIDATION_VALUE": True}
    server = _server_with_required_prompt()
    out = handle_prompts_get({"name": "echo", "arguments": {"unrelated": "x"}}, _ctx_for(server))
    assert isinstance(out, JsonRpcError)
    assert out.data == {"missing": ["who"], "value": {"unrelated": "x"}}


async def test_async_prompts_get_missing_default_omits_value(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    server = _server_with_required_prompt()
    out = await handle_prompts_get_async({"name": "echo", "arguments": {}}, _ctx_for(server))
    assert isinstance(out, JsonRpcError)
    assert out.data == {"missing": ["who"]}


async def test_async_prompts_get_missing_echo(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_VALIDATION_VALUE": True}
    server = _server_with_required_prompt()
    out = await handle_prompts_get_async({"name": "echo", "arguments": {"u": 2}}, _ctx_for(server))
    assert isinstance(out, JsonRpcError)
    assert out.data == {"missing": ["who"], "value": {"u": 2}}
