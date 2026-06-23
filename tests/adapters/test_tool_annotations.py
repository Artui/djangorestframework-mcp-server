"""MCP tool annotations are auto-derived from each tool's mutation profile.

Selector tools advertise ``readOnlyHint``; service tools advertise
``destructiveHint``; a chain is read-only only when every step is a
selector. Hints passed explicitly at registration override the derived
defaults. The values land both on ``binding.annotations`` (the single
source of truth) and on the ``tools/list`` wire payload.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.adapters.utils import merge_tool_annotations
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _annotations_for(server: MCPServer, name: str) -> Any:
    out: Any = handle_tools_list(None, _ctx(server))
    tool = next(t for t in out["tools"] if t["name"] == name)
    return tool.get("annotations")


# ---------- merge_tool_annotations (unit) ----------


def test_read_only_derives_only_read_only_hint() -> None:
    # destructive / idempotent are spec-meaningful only when not read-only,
    # so they are deliberately absent here.
    assert merge_tool_annotations(None, read_only=True) == {"readOnlyHint": True}


def test_mutation_derives_destructive_hint() -> None:
    assert merge_tool_annotations(None, read_only=False) == {
        "readOnlyHint": False,
        "destructiveHint": True,
    }


def test_explicit_hints_override_derived_defaults() -> None:
    merged = merge_tool_annotations(
        {"destructiveHint": False, "idempotentHint": True}, read_only=False
    )
    assert merged == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
    }


def test_explicit_title_rides_alongside_a_read_only_tool() -> None:
    assert merge_tool_annotations({"title": "List things"}, read_only=True) == {
        "readOnlyHint": True,
        "title": "List things",
    }


# ---------- end to end through tools/list ----------


def test_selector_tool_is_read_only() -> None:
    server = _server()
    binding = server.register_selector_tool(
        name="things.list",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
    )
    assert binding.annotations == {"readOnlyHint": True}
    assert _annotations_for(server, "things.list") == {"readOnlyHint": True}


def test_service_tool_is_destructive() -> None:
    server = _server()
    binding = server.register_service_tool(
        name="things.create",
        spec=ServiceSpec(service=lambda **_: {}, atomic=False),
    )
    assert binding.annotations == {"readOnlyHint": False, "destructiveHint": True}
    assert _annotations_for(server, "things.create") == {
        "readOnlyHint": False,
        "destructiveHint": True,
    }


def test_service_tool_per_spec_override_flips_the_hints() -> None:
    server = _server()
    server.register_service_tool(
        name="things.upsert",
        spec=ServiceSpec(service=lambda **_: {}, atomic=False),
        annotations={"destructiveHint": False, "idempotentHint": True},
    )
    assert _annotations_for(server, "things.upsert") == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
    }


def test_chain_of_only_selectors_is_read_only() -> None:
    server = _server()
    binding = server.register_chain_tool(
        name="read.chain",
        steps=[
            ChainStep("a", SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: {})),
            ChainStep("b", SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: {})),
        ],
    )
    assert binding.annotations == {"readOnlyHint": True}
    assert _annotations_for(server, "read.chain") == {"readOnlyHint": True}


def test_chain_with_any_service_step_is_destructive() -> None:
    server = _server()
    binding = server.register_chain_tool(
        name="write.chain",
        steps=[
            ChainStep("a", SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: {})),
            ChainStep("b", ServiceSpec(service=lambda **_: {}, atomic=False)),
        ],
    )
    assert binding.annotations == {"readOnlyHint": False, "destructiveHint": True}
    assert _annotations_for(server, "write.chain") == {
        "readOnlyHint": False,
        "destructiveHint": True,
    }
