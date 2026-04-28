from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.utils import check_permissions
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_binding import ResourceBinding
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_binding import ToolBinding
from rest_framework_mcp.registry.tool_registry import ToolRegistry


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
