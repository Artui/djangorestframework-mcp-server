"""Reflected selector shape reaches MCP dispatch — parity with the PAI toolset.

drf-services' ``spec_to_json_schema`` reflects a selector's parameters and an
``**extras: Unpack[TypedDict]`` into the tool schema; the MCP transport now folds
that into **both** the advertised ``inputSchema`` and the unknown-argument
"known" set, so a scoping key a selector reads from its ``extras`` is
discoverable *and* accepted over MCP without an explicit ``UrlKwarg`` — exactly
as it already is over the in-process Pydantic-AI ``SpecToolset``.

The two halves matter in different places: the schema half fixes discoverability
everywhere; the known-set half prevents an *advertised-but-rejected* argument on
a **closed** selector (an ``input_serializer`` under ``REJECT``), the only shape
whose dispatch actually runs the unknown-argument check.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework import serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from typing_extensions import TypedDict, Unpack

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import UnknownArguments
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding


def _ctx(*, tools: ToolRegistry) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user="alice"),
        tools=tools,
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


class _CeilingExtras(TypedDict):
    ceiling: int


def _extras_list_selector(user: Any, **extras: Unpack[_CeilingExtras]) -> list[int]:
    """A LIST selector scoped by a ``ceiling`` it reads from its typed extras."""
    ceiling = extras.get("ceiling")
    return [n for n in (1, 5, 15) if ceiling is not None and n <= int(ceiling)]


def test_unpack_extra_delivered_without_url_kwarg() -> None:
    # Serializer-less selector: reflection makes ``ceiling`` discoverable (the
    # schema half); the value flows through ``params`` into the selector's
    # ``**extras`` — no ``UrlKwarg`` needed.
    tools = ToolRegistry()
    tools.register(
        SelectorToolBinding(
            name="t",
            description=None,
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=_extras_list_selector),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {"ceiling": 5}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == [1, 5]


class _NoteSerializer(serializers.Serializer):
    note = serializers.CharField(required=False)


def test_reflected_extra_accepted_under_reject_with_serializer() -> None:
    # Closed selector (input_serializer + REJECT): without the known-set half,
    # ``ceiling`` would be flagged as an unknown argument even though it is
    # advertised. Reflection adds it to the known set, so it is accepted and
    # delivered to ``**extras`` alongside the validated serializer field.
    tools = ToolRegistry()
    tools.register(
        SelectorToolBinding(
            name="t",
            description=None,
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=_extras_list_selector),
            input_serializer=_NoteSerializer,
            unknown_arguments=UnknownArguments.REJECT,
        )
    )
    out = handle_tools_call(
        {"name": "t", "arguments": {"ceiling": 5, "note": "hi"}}, _ctx(tools=tools)
    )
    assert isinstance(out, dict)
    assert out["structuredContent"] == [1, 5]
