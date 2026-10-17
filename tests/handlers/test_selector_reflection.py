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
from django_service_specs import SelectorKind
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services.types.selector_spec import SelectorSpec
from typing_extensions import TypedDict, Unpack

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
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
        token=TokenInfo(user=_ALICE),
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


class _IdentifiedUser(str):
    """A user that is both a value and an identity.

    These tests assert on the user *value* (``== "alice"``, and it is rendered
    into tool output), while ``principal_for_token`` needs a ``pk`` — an
    authenticated caller without one is refused, because every such caller would
    share the ``"anonymous"`` principal and therefore each other's sessions.
    A ``str`` subclass satisfies both without changing a single assertion.
    """

    pk = "alice"


_ALICE = _IdentifiedUser("alice")


# ---------- a ``UrlKwarg`` beside a reflected key ----------


def _bare_list_selector(user: Any, **extras: Any) -> list[int]:
    """The same read with an untyped catch-all, which names no key."""
    ceiling = extras.get("ceiling")
    return [n for n in (1, 5, 15) if ceiling is not None and n <= int(ceiling)]


def _url_kwarg_server(selector: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="t",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector, permission_classes=[AllowAny]),
        url_kwargs=(UrlKwarg("ceiling", type="integer"),),
    )
    return server


def test_a_url_kwarg_beside_a_reflected_key_still_reaches_the_typed_extras() -> None:
    # The documented superset: registering the ``UrlKwarg`` routes the value
    # through ``view.kwargs`` and the selector still reads it from its extras,
    # under the one property the schema lists.
    server = _url_kwarg_server(_extras_list_selector)

    result = server.call_tool("t", {"ceiling": 5}, user=None).to_dict()
    listed: Any = server.list_tools(user=None)

    assert result["structuredContent"] == [1, 5]
    assert set(listed["tools"][0]["inputSchema"]["properties"]) == {"ceiling"}


def test_a_url_kwarg_does_not_reach_an_untyped_catch_all() -> None:
    # drf-services 0.58.0 hands a bare ``**kwargs`` the declared arguments and
    # the seeds, never a key it does not declare, and a route capture is no
    # declaration of the callable's: the selector reads ``view.kwargs`` or
    # names the key, or it sees nothing.
    server = _url_kwarg_server(_bare_list_selector)

    result = server.call_tool("t", {"ceiling": 5}, user=None).to_dict()

    assert result["structuredContent"] == []
