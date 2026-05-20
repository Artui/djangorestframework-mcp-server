from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding


@dataclass
class _BareDC:
    name: str


def _ctx(tools: ToolRegistry, resources: ResourceRegistry | None = None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=tools,
        resources=resources or ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


# ---------- tools/call async ----------


async def test_async_tools_call_rejects_non_dict_params() -> None:
    out = await handle_tools_call_async(None, _ctx(ToolRegistry()))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_tools_call_rejects_missing_name() -> None:
    out = await handle_tools_call_async({}, _ctx(ToolRegistry()))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_tools_call_rejects_non_string_name() -> None:
    out = await handle_tools_call_async({"name": 7}, _ctx(ToolRegistry()))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_tools_call_unknown_tool() -> None:
    out = await handle_tools_call_async({"name": "nope"}, _ctx(ToolRegistry()))
    assert isinstance(out, JsonRpcError) and out.code == -32004


async def test_async_tools_call_rejects_non_dict_arguments() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t", description=None, spec=ServiceSpec(service=lambda: None, atomic=False)
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": [1, 2]}, _ctx(tools))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_tools_call_invalid_input() -> None:
    """A bare-dataclass input that fails validation surfaces the detail."""

    def svc(*, data: _BareDC) -> dict[str, Any]:
        return {"name": data.name}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, input_serializer=_BareDC, atomic=False),
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, JsonRpcError) and out.code == -32602
    assert "name" in out.data["detail"]


async def test_async_tools_call_translates_service_validation_error() -> None:
    def svc() -> None:
        raise ServiceValidationError({"f": ["bad"]})

    tools = ToolRegistry()
    tools.register(
        ToolBinding(name="t", description=None, spec=ServiceSpec(service=svc, atomic=False))
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, JsonRpcError) and out.code == -32602
    assert out.data == {"detail": {"f": ["bad"]}}


async def test_async_tools_call_translates_service_error() -> None:
    from rest_framework_services.exceptions.service_error import ServiceError

    def svc() -> None:
        raise ServiceError("nope")

    tools = ToolRegistry()
    tools.register(
        ToolBinding(name="t", description=None, spec=ServiceSpec(service=svc, atomic=False))
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, JsonRpcError) and out.code == -32000


class _DenyEveryone:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return False

    def required_scopes(self) -> list[str]:
        return ["x"]


async def test_async_tools_call_denied_by_permission() -> None:
    def svc() -> None:
        return None

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False),
            permissions=(_DenyEveryone(),),
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, JsonRpcError) and out.code == -32002


async def test_async_tools_call_runs_async_service_natively() -> None:
    """A coroutine-function service is awaited directly, not wrapped in a thread."""

    async def aproduce() -> dict[str, Any]:
        return {"async": True}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(name="t", description=None, spec=ServiceSpec(service=aproduce, atomic=False))
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"async": True}


async def test_async_tools_call_with_async_output_selector() -> None:
    def svc() -> dict[str, Any]:
        return {"raw": True}

    async def shape(*, result: dict[str, Any]) -> dict[str, Any]:
        return {"shaped": result["raw"]}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, output_selector=shape, atomic=False),
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"shaped": True}


# ---------- resources/read async ----------


async def test_async_resources_read_rejects_non_dict_params() -> None:
    out = await handle_resources_read_async(None, _ctx(ToolRegistry()))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_resources_read_rejects_missing_uri() -> None:
    out = await handle_resources_read_async({}, _ctx(ToolRegistry()))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_resources_read_unknown_uri() -> None:
    out = await handle_resources_read_async({"uri": "nope://x"}, _ctx(ToolRegistry()))
    assert isinstance(out, JsonRpcError) and out.code == -32003


async def test_async_resources_read_denied_by_permission() -> None:
    def selector() -> dict[str, Any]:
        return {}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://",
            description=None,
            selector=selector,
            permissions=(_DenyEveryone(),),
        )
    )
    out = await handle_resources_read_async({"uri": "r://"}, _ctx(ToolRegistry(), resources))
    assert isinstance(out, JsonRpcError) and out.code == -32002


async def test_async_resources_read_passes_through_when_no_serializer() -> None:
    def selector(*, pk: str) -> dict[str, Any]:
        return {"pk": pk}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(name="r", uri_template="r://{pk}", description=None, selector=selector)
    )
    out = await handle_resources_read_async({"uri": "r://7"}, _ctx(ToolRegistry(), resources))
    assert isinstance(out, dict)
    assert '"pk": "7"' in out["contents"][0]["text"]


async def test_async_dispatch_routes_prompts_get_to_async_handler() -> None:
    """``adispatch`` recognises ``prompts/get`` and routes to the async handler."""
    from rest_framework_mcp.handlers.async_dispatch import adispatch
    from rest_framework_mcp.registry.prompt_registry import PromptRegistry
    from rest_framework_mcp.registry.types.prompt_binding import PromptBinding

    prompts = PromptRegistry()
    prompts.register(PromptBinding(name="p", description=None, render=lambda **_: "ok"))
    ctx = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        prompts=prompts,
        protocol_version="2025-11-25",
    )
    out = await adispatch("prompts/get", {"name": "p", "arguments": {}}, ctx)
    assert isinstance(out, dict)
    assert out["messages"][0]["content"]["text"] == "ok"


async def test_async_resources_read_runs_async_selector_natively() -> None:
    async def aselector(*, pk: str) -> dict[str, Any]:
        return {"async": True, "pk": pk}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(name="r", uri_template="r://{pk}", description=None, selector=aselector)
    )
    out = await handle_resources_read_async({"uri": "r://9"}, _ctx(ToolRegistry(), resources))
    assert isinstance(out, dict)
    assert '"async": true' in out["contents"][0]["text"]


async def test_async_tools_call_honors_include_structured_content_override() -> None:
    """The per-binding override threads through the async dispatch path."""

    def svc() -> dict[str, Any]:
        return {"a": 1}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False),
            include_structured_content=False,
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, dict)
    assert "structuredContent" not in out


# ---------- spec context plumbing (sister-repo 0.12+) ----------


async def test_async_input_serializer_context_flows_into_validation() -> None:
    received: dict[str, Any] = {}

    class _CtxAwareInput(drf_serializers.Serializer):
        x = drf_serializers.IntegerField()

        def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
            received.update(self.context)
            return attrs

    def svc(*, data: dict[str, Any]) -> dict[str, Any]:
        return {"got": data["x"]}

    def ctx_provider(view: Any, request: Any) -> Mapping[str, Any]:  # noqa: ARG001
        return {"k": "v"}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(
                service=svc,
                input_serializer=_CtxAwareInput,
                input_serializer_context=ctx_provider,
                atomic=False,
            ),
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {"x": 1}}, _ctx(tools))
    assert isinstance(out, dict)
    assert received == {"k": "v"}


async def test_async_output_serializer_context_flows_into_render() -> None:
    class _Out(drf_serializers.Serializer):
        tag = drf_serializers.SerializerMethodField()

        def get_tag(self, _: Any) -> str:
            return self.context["who"]

    def svc() -> dict[str, str]:
        return {}

    def ctx_provider(view: Any, request: Any) -> Mapping[str, Any]:  # noqa: ARG001
        return {"who": "async"}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(
                service=svc,
                output_serializer=_Out,
                output_serializer_context=ctx_provider,
                atomic=False,
            ),
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"tag": "async"}
