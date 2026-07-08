"""End-to-end coverage: ``FILTER_LISTINGS_BY_PERMISSIONS`` toggle.

Exercises every list handler (``tools/list``, ``resources/list``,
``resources/templates/list``, ``prompts/list``) with the setting on
and off, plus the ``always_listed=True`` opt-back-in and the
cursor-pagination interaction.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_prompts_list import handle_prompts_list
from rest_framework_mcp.handlers.handle_resources_list import handle_resources_list
from rest_framework_mcp.handlers.handle_resources_templates_list import (
    handle_resources_templates_list,
)
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.prompt_binding import PromptBinding
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding


class _DenyAll:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:  # noqa: ARG002
        return False

    def required_scopes(self) -> list[str]:
        return []


def _svc() -> None:
    return None


def _sel() -> list[Any]:
    return []


def _render() -> str:
    return "x"


def _ctx(
    *,
    tools: ToolRegistry | None = None,
    resources: ResourceRegistry | None = None,
    prompts: PromptRegistry | None = None,
) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=tools or ToolRegistry(),
        resources=resources or ResourceRegistry(),
        prompts=prompts or PromptRegistry(),
        protocol_version="2025-11-25",
    )


# ---------- tools/list ----------


def _registry_with(*bindings: ToolBinding) -> ToolRegistry:
    registry = ToolRegistry()
    for b in bindings:
        registry.register(b)
    return registry


def _tool(
    name: str, *, permissions: tuple[Any, ...] = (), always_listed: bool = False
) -> ToolBinding:
    return ToolBinding(
        name=name,
        description=None,
        spec=ServiceSpec(service=_svc, atomic=False),
        permissions=permissions,
        always_listed=always_listed,
    )


def test_tools_list_setting_off_returns_all_bindings_even_when_denied(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": False}
    tools = _registry_with(_tool("a"), _tool("b", permissions=(_DenyAll(),)))
    out = handle_tools_list(None, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert {t["name"] for t in out["tools"]} == {"a", "b"}


def test_tools_list_setting_on_hides_denied_bindings(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    tools = _registry_with(_tool("a"), _tool("b", permissions=(_DenyAll(),)))
    out = handle_tools_list(None, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert {t["name"] for t in out["tools"]} == {"a"}


def test_tools_list_always_listed_overrides_denial(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    tools = _registry_with(
        _tool("a"),
        _tool("b", permissions=(_DenyAll(),), always_listed=True),
    )
    out = handle_tools_list(None, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert {t["name"] for t in out["tools"]} == {"a", "b"}


def test_tools_list_filter_runs_before_pagination(settings) -> None:
    """``nextCursor`` reflects the visible slice, not the global registry."""
    settings.REST_FRAMEWORK_MCP = {
        "FILTER_LISTINGS_BY_PERMISSIONS": True,
        "PAGE_SIZE": 2,
    }
    tools = _registry_with(
        _tool("denied-1", permissions=(_DenyAll(),)),
        _tool("denied-2", permissions=(_DenyAll(),)),
        _tool("visible-1"),
        _tool("visible-2"),
        _tool("visible-3"),
    )
    out = handle_tools_list(None, _ctx(tools=tools))
    assert isinstance(out, dict)
    page_names = [t["name"] for t in out["tools"]]
    assert page_names == ["visible-1", "visible-2"]
    # Filter ran before pagination — cursor reflects the post-filter
    # offset into the visible slice (3 visible total, page size 2 →
    # one more page expected).
    assert "nextCursor" in out


# ---------- resources/list ----------


def _resource(name: str, *, permissions: tuple[Any, ...] = ()) -> ResourceBinding:
    return ResourceBinding(
        name=name,
        uri_template=f"r://{name}",
        description=None,
        selector=_sel,
        kind=SelectorKind.LIST,
        permissions=permissions,
    )


def test_resources_list_setting_on_hides_denied(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    resources = ResourceRegistry()
    resources.register(_resource("a"))
    resources.register(_resource("b", permissions=(_DenyAll(),)))
    out = handle_resources_list(None, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert {r["name"] for r in out["resources"]} == {"a"}


def test_resources_list_setting_off_returns_all(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": False}
    resources = ResourceRegistry()
    resources.register(_resource("a", permissions=(_DenyAll(),)))
    out = handle_resources_list(None, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert {r["name"] for r in out["resources"]} == {"a"}


# ---------- resources/templates/list ----------


def _template_resource(name: str, *, permissions: tuple[Any, ...] = ()) -> ResourceBinding:
    return ResourceBinding(
        name=name,
        uri_template=f"r://{name}/{{pk}}",
        description=None,
        selector=_sel,
        kind=SelectorKind.LIST,
        permissions=permissions,
    )


def test_resources_templates_list_setting_on_hides_denied(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    resources = ResourceRegistry()
    resources.register(_template_resource("a"))
    resources.register(_template_resource("b", permissions=(_DenyAll(),)))
    out = handle_resources_templates_list(None, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert {t["name"] for t in out["resourceTemplates"]} == {"a"}


def test_resources_templates_list_setting_off_returns_all(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": False}
    resources = ResourceRegistry()
    resources.register(_template_resource("a", permissions=(_DenyAll(),)))
    out = handle_resources_templates_list(None, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert {t["name"] for t in out["resourceTemplates"]} == {"a"}


# ---------- prompts/list ----------


def _prompt(name: str, *, permissions: tuple[Any, ...] = ()) -> PromptBinding:
    return PromptBinding(
        name=name,
        description=None,
        render=_render,
        permissions=permissions,
    )


def test_prompts_list_setting_on_hides_denied(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    prompts = PromptRegistry()
    prompts.register(_prompt("a"))
    prompts.register(_prompt("b", permissions=(_DenyAll(),)))
    out = handle_prompts_list(None, _ctx(prompts=prompts))
    assert isinstance(out, dict)
    assert {p["name"] for p in out["prompts"]} == {"a"}


def test_prompts_list_setting_off_returns_all(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": False}
    prompts = PromptRegistry()
    prompts.register(_prompt("a", permissions=(_DenyAll(),)))
    out = handle_prompts_list(None, _ctx(prompts=prompts))
    assert isinstance(out, dict)
    assert {p["name"] for p in out["prompts"]} == {"a"}
