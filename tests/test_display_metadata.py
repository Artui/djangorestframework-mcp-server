"""``display_name`` / ``display_description`` — consumer-only tool metadata.

These fields are carried from registration onto the binding for downstream
libraries to read; the MCP server itself never emits them on the wire.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import (
    ChainStep,
    MCPServer,
    ToolDefinition,
    register_tools,
)
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _svc_spec() -> ServiceSpec:
    return ServiceSpec(service=lambda **_: {}, atomic=False)


def _sel_spec() -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.LIST, selector=lambda: [])


def test_service_tool_carries_display_metadata() -> None:
    b = _server().register_service_tool(
        name="t", spec=_svc_spec(), display_name="Nice Tool", display_description="Does nice things"
    )
    assert b.display_name == "Nice Tool"
    assert b.display_description == "Does nice things"


def test_selector_tool_carries_display_metadata() -> None:
    b = _server().register_selector_tool(
        name="t", spec=_sel_spec(), display_name="Lister", display_description="Lists stuff"
    )
    assert b.display_name == "Lister"
    assert b.display_description == "Lists stuff"


def test_chain_tool_carries_display_metadata() -> None:
    b = _server().register_chain_tool(
        name="t",
        steps=[ChainStep("a", _svc_spec())],
        display_name="Flow",
        display_description="A flow",
    )
    assert b.display_name == "Flow"
    assert b.display_description == "A flow"


def test_defaults_to_none_when_unset() -> None:
    b = _server().register_service_tool(name="t", spec=_svc_spec())
    assert b.display_name is None
    assert b.display_description is None


def test_tool_definition_factories_carry_metadata() -> None:
    svc = ToolDefinition.service(
        name="s", spec=_svc_spec(), display_name="S", display_description="sd"
    )
    sel = ToolDefinition.selector(
        name="l", spec=_sel_spec(), display_name="L", display_description="ld"
    )
    assert (svc.display_name, svc.display_description) == ("S", "sd")
    assert (sel.display_name, sel.display_description) == ("L", "ld")


def test_register_tools_forwards_metadata_to_bindings() -> None:
    server = _server()
    bindings = register_tools(
        server,
        definitions=[
            ToolDefinition.service(
                name="s", spec=_svc_spec(), display_name="S", display_description="sd"
            )
        ],
    )
    assert bindings[0].display_name == "S"
    assert bindings[0].display_description == "sd"


def test_display_metadata_is_not_emitted_on_the_wire() -> None:
    server = _server()
    server.register_service_tool(
        name="t", spec=_svc_spec(), display_name="Hidden", display_description="Secret"
    )
    ctx = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )
    out: Any = handle_tools_list(None, ctx)
    tool = out["tools"][0]
    serialized = str(tool)
    assert "display_name" not in serialized
    assert "displayName" not in serialized
    assert "Hidden" not in serialized
    assert "Secret" not in serialized
