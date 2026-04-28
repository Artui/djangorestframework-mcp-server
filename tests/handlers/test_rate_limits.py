"""Per-binding rate limit wiring through tools/call, resources/read, prompts/get."""

from __future__ import annotations

from django.http import HttpRequest
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_prompts_get import handle_prompts_get
from rest_framework_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.utils import consume_rate_limits
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.prompt_binding import PromptBinding
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_binding import ResourceBinding
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_binding import ToolBinding
from rest_framework_mcp.registry.tool_registry import ToolRegistry


class _AlwaysDeny:
    """Rate limiter that always reports the limit is exhausted."""

    def __init__(self, retry: int = 30) -> None:
        self._retry = retry

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        return self._retry


class _AlwaysAllow:
    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        return None


def _ctx(*, tools=None, resources=None, prompts=None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=tools or ToolRegistry(),
        resources=resources or ResourceRegistry(),
        prompts=prompts or PromptRegistry(),
        protocol_version="2025-11-25",
    )


# ---------- consume_rate_limits ----------


def test_consume_rate_limits_returns_none_when_all_allow() -> None:
    assert (
        consume_rate_limits((_AlwaysAllow(), _AlwaysAllow()), HttpRequest(), TokenInfo(user=None))
        is None
    )


def test_consume_rate_limits_short_circuits_on_first_denial() -> None:
    out = consume_rate_limits(
        (_AlwaysDeny(retry=5), _AlwaysAllow()), HttpRequest(), TokenInfo(user=None)
    )
    assert out == 5


def test_consume_rate_limits_empty_tuple_allows() -> None:
    assert consume_rate_limits((), HttpRequest(), TokenInfo(user=None)) is None


# ---------- tools/call ----------


def test_tools_call_returns_rate_limited_envelope() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=lambda: None, atomic=False),
            rate_limits=(_AlwaysDeny(retry=42),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32005
    assert out.data == {"retryAfter": 42}


async def test_async_tools_call_returns_rate_limited_envelope() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=lambda: None, atomic=False),
            rate_limits=(_AlwaysDeny(retry=11),),
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32005


# ---------- resources/read ----------


def test_resources_read_returns_rate_limited_envelope() -> None:
    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://",
            description=None,
            selector=lambda: {},
            rate_limits=(_AlwaysDeny(retry=7),),
        )
    )
    out = handle_resources_read({"uri": "r://"}, _ctx(resources=resources))
    assert isinstance(out, JsonRpcError) and out.code == -32005


async def test_async_resources_read_returns_rate_limited_envelope() -> None:
    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://",
            description=None,
            selector=lambda: {},
            rate_limits=(_AlwaysDeny(retry=9),),
        )
    )
    out = await handle_resources_read_async({"uri": "r://"}, _ctx(resources=resources))
    assert isinstance(out, JsonRpcError) and out.code == -32005


# ---------- prompts/get ----------


def test_prompts_get_returns_rate_limited_envelope() -> None:
    prompts = PromptRegistry()
    prompts.register(
        PromptBinding(
            name="p",
            description=None,
            render=lambda **_: "x",
            rate_limits=(_AlwaysDeny(retry=3),),
        )
    )
    out = handle_prompts_get({"name": "p", "arguments": {}}, _ctx(prompts=prompts))
    assert isinstance(out, JsonRpcError) and out.code == -32005


async def test_async_prompts_get_returns_rate_limited_envelope() -> None:
    prompts = PromptRegistry()
    prompts.register(
        PromptBinding(
            name="p",
            description=None,
            render=lambda **_: "x",
            rate_limits=(_AlwaysDeny(retry=3),),
        )
    )
    out = await handle_prompts_get_async({"name": "p", "arguments": {}}, _ctx(prompts=prompts))
    assert isinstance(out, JsonRpcError) and out.code == -32005
