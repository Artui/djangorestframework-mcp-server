from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import check_permissions
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding


@dataclass
class _BareDC:
    name: str
    qty: int = 0


def _ctx(tools: ToolRegistry, resources: ResourceRegistry | None = None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=tools,
        resources=resources or ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


def test_tools_call_with_bare_dataclass_input() -> None:
    """The dataclass input is auto-wrapped in a DataclassSerializer."""

    def svc(*, data: _BareDC) -> dict[str, Any]:
        return {"name": data.name, "qty": data.qty}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, input_serializer=_BareDC, atomic=False),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {"name": "x", "qty": 3}}, _ctx(tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"name": "x", "qty": 3}


def test_tools_call_returns_empty_dict_when_service_returns_none() -> None:
    """Tools that return None must still produce an object as ``structuredContent``."""

    def svc() -> None:
        return None

    tools = ToolRegistry()
    tools.register(
        ToolBinding(name="t", description=None, spec=ServiceSpec(service=svc, atomic=False))
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {}


def test_tools_call_translates_service_validation_error() -> None:
    def svc() -> None:
        raise ServiceValidationError({"field": ["bad"]})

    tools = ToolRegistry()
    tools.register(
        ToolBinding(name="t", description=None, spec=ServiceSpec(service=svc, atomic=False))
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602
    assert out.data == {"detail": {"field": ["bad"]}}


class _DenyEveryone:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return False

    def required_scopes(self) -> list[str]:
        return ["scope:x"]


def test_tools_call_denied_by_permission() -> None:
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
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32002
    assert out.data == {"requiredScopes": ["scope:x"]}


def test_tools_call_with_output_selector() -> None:
    """The optional ``output_selector`` runs after the service returns."""

    def svc() -> dict[str, Any]:
        return {"raw": True}

    def shape(*, result: dict[str, Any]) -> dict[str, Any]:
        return {"shaped": result["raw"]}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, output_selector=shape, atomic=False),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"shaped": True}


def test_resources_read_passes_through_when_no_output_serializer() -> None:
    def selector(*, pk: str) -> dict[str, Any]:
        return {"pk": pk}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{pk}",
            description=None,
            selector=selector,
        )
    )
    ctx = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=ToolRegistry(),
        resources=resources,
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )
    out = handle_resources_read({"uri": "r://7"}, ctx)
    assert isinstance(out, dict)
    assert '"pk": "7"' in out["contents"][0]["text"]


def test_resources_read_denied_by_permission() -> None:
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
    ctx = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=ToolRegistry(),
        resources=resources,
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )
    out = handle_resources_read({"uri": "r://"}, ctx)
    assert isinstance(out, JsonRpcError)
    assert out.code == -32002
    assert out.data == {"requiredScopes": ["scope:x"]}


def test_check_permissions_no_required_scopes() -> None:
    """Denial without ``required_scopes`` returns an empty list."""

    class _DenyNoScopes:
        def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
            return False

        def required_scopes(self) -> list[str]:
            return []

    allowed, scopes = check_permissions((_DenyNoScopes(),), HttpRequest(), TokenInfo(user=None))
    assert allowed is False
    assert scopes == []


def test_check_permissions_all_allow() -> None:
    class _AllowAll:
        def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
            return True

        def required_scopes(self) -> list[str]:
            return []

    allowed, scopes = check_permissions((_AllowAll(),), HttpRequest(), TokenInfo(user=None))
    assert allowed is True
    assert scopes == []


def test_check_permissions_empty_tuple() -> None:
    allowed, scopes = check_permissions((), HttpRequest(), TokenInfo(user=None))
    assert allowed is True
    assert scopes == []


def test_tools_call_omits_structured_content_when_global_disabled(settings) -> None:
    # Disabling structuredContent also requires disabling outputSchema —
    # advertising the schema while suppressing the content is a spec
    # violation that ``resolve_structured_output`` rejects.
    settings.REST_FRAMEWORK_MCP = {
        "INCLUDE_STRUCTURED_CONTENT": False,
        "INCLUDE_OUTPUT_SCHEMA": False,
    }

    def svc() -> dict[str, Any]:
        return {"a": 1}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(name="t", description=None, spec=ServiceSpec(service=svc, atomic=False))
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, dict)
    assert "structuredContent" not in out
    # The text rendering still carries the payload.
    assert "a" in out["content"][0]["text"]


def test_tools_call_per_binding_override_forces_off_when_global_on(settings) -> None:
    # Per-binding override must drop the schema in lockstep — otherwise
    # the resolver raises at request time.
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_STRUCTURED_CONTENT": True}

    def svc() -> dict[str, Any]:
        return {"a": 1}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False),
            include_structured_content=False,
            include_output_schema=False,
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, dict)
    assert "structuredContent" not in out


def test_tools_call_per_binding_override_forces_on_when_global_off(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_STRUCTURED_CONTENT": False}

    def svc() -> dict[str, Any]:
        return {"a": 1}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False),
            include_structured_content=True,
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"a": 1}


def test_tools_list_drops_output_schema_when_explicitly_disabled() -> None:
    """``include_output_schema=False`` suppresses the schema even with an output_serializer."""
    from rest_framework import serializers as drf_serializers

    class _Ser(drf_serializers.Serializer):
        a = drf_serializers.IntegerField()

    def svc() -> dict[str, Any]:
        return {"a": 1}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, output_serializer=_Ser, atomic=False),
            include_output_schema=False,
        )
    )
    out = handle_tools_list(None, _ctx(tools))
    assert isinstance(out, dict)
    tool = out["tools"][0]
    assert "outputSchema" not in tool


def test_tools_list_drops_output_schema_when_global_setting_disabled(settings) -> None:
    """``INCLUDE_OUTPUT_SCHEMA=False`` server-wide suppresses the schema for all bindings."""
    from rest_framework import serializers as drf_serializers

    settings.REST_FRAMEWORK_MCP = {"INCLUDE_OUTPUT_SCHEMA": False}

    class _Ser(drf_serializers.Serializer):
        a = drf_serializers.IntegerField()

    def svc() -> dict[str, Any]:
        return {"a": 1}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, output_serializer=_Ser, atomic=False),
        )
    )
    out = handle_tools_list(None, _ctx(tools))
    assert isinstance(out, dict)
    tool = out["tools"][0]
    assert "outputSchema" not in tool
    # structuredContent is still emitted — that direction is spec-allowed.
    call_out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools))
    assert isinstance(call_out, dict)
    assert call_out["structuredContent"] == {"a": 1}


def test_tools_list_emits_output_schema_when_structured_content_enabled() -> None:
    """The default path still ships outputSchema when an output_serializer exists."""
    from rest_framework import serializers as drf_serializers

    class _Ser(drf_serializers.Serializer):
        a = drf_serializers.IntegerField()

    def svc() -> dict[str, Any]:
        return {"a": 1}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, output_serializer=_Ser, atomic=False),
        )
    )
    out = handle_tools_list(None, _ctx(tools))
    assert isinstance(out, dict)
    tool = out["tools"][0]
    assert "outputSchema" in tool


# ---------- argument_binding: native parameter binding (Phase 10a) ----------


def test_service_tool_with_merge_binding_spreads_args_to_callable_params() -> None:
    """``ArgumentBinding.MERGE`` lets a service declare individual params."""
    from rest_framework_mcp.constants import ArgumentBinding

    def svc(*, project_id: str, expand: bool = False) -> dict[str, Any]:
        return {"pid": project_id, "expand": expand}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False),
            argument_binding=ArgumentBinding.MERGE,
        )
    )
    out = handle_tools_call(
        {"name": "t", "arguments": {"project_id": "p1", "expand": True}}, _ctx(tools)
    )
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"pid": "p1", "expand": True}


def test_service_tool_with_replace_binding_lets_client_override_provider() -> None:
    from rest_framework_mcp.constants import ArgumentBinding

    def provider(view: Any, request: Any) -> dict[str, Any]:  # noqa: ARG001
        return {"page_size": 50}

    def svc(*, page_size: int) -> dict[str, int]:
        return {"got": page_size}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False, kwargs=provider),
            argument_binding=ArgumentBinding.REPLACE,
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {"page_size": 200}}, _ctx(tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"got": 200}


def test_service_tool_merge_pool_seeds_cannot_be_overridden_by_client() -> None:
    """Reserved pool-seed keys (``user`` / ``request`` / ``data``) are stripped from spread."""
    from rest_framework_mcp.constants import ArgumentBinding

    received: dict[str, Any] = {}

    def svc(*, user: Any, ok: int) -> dict[str, Any]:
        received["user"] = user
        received["ok"] = ok
        return {}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False),
            argument_binding=ArgumentBinding.MERGE,
        )
    )
    handle_tools_call({"name": "t", "arguments": {"user": "evil", "ok": 1}}, _ctx(tools))
    # User came from the transport (None in test), not from the client.
    assert received["user"] is None
    assert received["ok"] == 1
