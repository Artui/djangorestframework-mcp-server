"""The generic ``_meta`` bundle is threaded registration → binding → wire.

``_meta`` is the base protocol's open extension namespace. This package
carries it verbatim: nothing here validates, reserves, or interprets a key.
The registration parameter is spelled ``meta`` (the wire spelling ``_meta``
would read as private in Python) and appears on every registration surface
— imperative, decorator, and declarative — landing on the binding as the
single source of truth and on the corresponding listing payload.

:func:`merge_meta` is the one place contributions are combined, so a
feature that needs to inject its own key adds an argument at the adapter
call site rather than touching every binding.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.adapters.utils import merge_meta
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_prompts_list import handle_prompts_list
from rest_framework_mcp.handlers.handle_resources_list import handle_resources_list
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_resources_templates_list import (
    handle_resources_templates_list,
)
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.register_tools import register_tools
from rest_framework_mcp.registry.types.selector_defaults import SelectorDefaults
from rest_framework_mcp.registry.types.tool_definition import ToolDefinition

UI_META: dict[str, Any] = {"example.com/panel": {"href": "panel://invoices"}}


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


def _entry(payload: Any, key: str, name: str) -> dict[str, Any]:
    return next(item for item in payload[key] if item["name"] == name)


# ---------- merge_meta (unit) ----------


def test_merge_meta_with_no_pieces_is_empty() -> None:
    assert merge_meta() == {}


def test_merge_meta_skips_none_and_empty_pieces() -> None:
    assert merge_meta(None, {}, {"a": 1}, None) == {"a": 1}


def test_merge_meta_later_piece_wins_on_a_conflicting_key() -> None:
    assert merge_meta({"a": 1, "b": 2}, {"a": 9}) == {"a": 9, "b": 2}


def test_merge_meta_is_shallow_and_replaces_a_nested_bundle_outright() -> None:
    """Extension keys are opaque bundles owned by one extension.

    Deep-merging two of them would synthesise a shape neither owner
    declared, so the later piece replaces the earlier one wholesale.
    """
    merged = merge_meta({"ext": {"a": 1, "b": 2}}, {"ext": {"a": 9}})
    assert merged == {"ext": {"a": 9}}


def test_merge_meta_does_not_mutate_its_inputs() -> None:
    first: dict[str, Any] = {"a": 1}
    merged = merge_meta(first, {"b": 2})
    assert first == {"a": 1}
    assert merged is not first


# ---------- tools ----------


def test_service_tool_meta_reaches_binding_and_tools_list() -> None:
    server = _server()
    binding = server.register_service_tool(
        name="things.create",
        spec=ServiceSpec(service=lambda **_: {}, atomic=False),
        meta=UI_META,
    )
    assert binding.meta == UI_META
    assert _entry(handle_tools_list(None, _ctx(server)), "tools", "things.create")["_meta"] == (
        UI_META
    )


def test_selector_tool_meta_reaches_binding_and_tools_list() -> None:
    server = _server()
    binding = server.register_selector_tool(
        name="things.list",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
        meta=UI_META,
    )
    assert binding.meta == UI_META
    assert _entry(handle_tools_list(None, _ctx(server)), "tools", "things.list")["_meta"] == UI_META


def test_chain_tool_meta_reaches_binding_and_tools_list() -> None:
    server = _server()
    binding = server.register_chain_tool(
        name="things.chain",
        steps=[ChainStep("a", SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: {}))],
        meta=UI_META,
    )
    assert binding.meta == UI_META
    assert _entry(handle_tools_list(None, _ctx(server)), "tools", "things.chain")["_meta"] == (
        UI_META
    )


def test_a_tool_without_meta_emits_no_meta_key() -> None:
    server = _server()
    binding = server.register_service_tool(
        name="things.plain", spec=ServiceSpec(service=lambda **_: {}, atomic=False)
    )
    assert binding.meta == {}
    assert "_meta" not in _entry(handle_tools_list(None, _ctx(server)), "tools", "things.plain")


def test_meta_and_annotations_are_independent_bundles() -> None:
    """``_meta`` is the open extension namespace; ``annotations`` is not."""
    server = _server()
    server.register_service_tool(
        name="things.both",
        spec=ServiceSpec(service=lambda **_: {}, atomic=False),
        annotations={"destructiveHint": False},
        meta=UI_META,
    )
    entry = _entry(handle_tools_list(None, _ctx(server)), "tools", "things.both")
    assert entry["annotations"] == {"readOnlyHint": False, "destructiveHint": False}
    assert entry["_meta"] == UI_META


# ---------- resources ----------


def test_resource_meta_reaches_resources_list() -> None:
    server = _server()
    binding = server.register_resource(
        name="thing",
        uri_template="things://all",
        selector=SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
        meta=UI_META,
    )
    assert binding.meta == UI_META
    assert _entry(handle_resources_list(None, _ctx(server)), "resources", "thing")["_meta"] == (
        UI_META
    )


def test_resource_template_meta_reaches_templates_list() -> None:
    server = _server()
    server.register_resource(
        name="thing",
        uri_template="things://{pk}",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: {}),
        meta=UI_META,
    )
    payload = handle_resources_templates_list(None, _ctx(server))
    assert _entry(payload, "resourceTemplates", "thing")["_meta"] == UI_META


def test_resource_meta_rides_on_the_resources_read_contents_block() -> None:
    server = _server()
    server.register_resource(
        name="thing",
        uri_template="things://{pk}",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: {"pk": pk}),
        meta=UI_META,
    )
    out: Any = handle_resources_read({"uri": "things://7"}, _ctx(server))
    assert out["contents"][0]["_meta"] == UI_META


async def test_async_resources_read_carries_the_same_meta() -> None:
    """The async read handler is a parallel implementation — keep it in step."""
    server = _server()
    server.register_resource(
        name="thing",
        uri_template="things://{pk}",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: {"pk": pk}),
        meta=UI_META,
    )
    out: Any = await handle_resources_read_async({"uri": "things://7"}, _ctx(server))
    assert out["contents"][0]["_meta"] == UI_META


def test_a_resource_without_meta_emits_no_meta_key_anywhere() -> None:
    server = _server()
    server.register_resource(
        name="thing",
        uri_template="things://{pk}",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: {"pk": pk}),
    )
    templates = handle_resources_templates_list(None, _ctx(server))
    assert "_meta" not in _entry(templates, "resourceTemplates", "thing")
    out: Any = handle_resources_read({"uri": "things://7"}, _ctx(server))
    assert "_meta" not in out["contents"][0]


# ---------- prompts ----------


def test_prompt_meta_reaches_prompts_list() -> None:
    server = _server()
    binding = server.register_prompt(name="greet", render=lambda **_: "hi", meta=UI_META)
    assert binding.meta == UI_META
    assert _entry(handle_prompts_list(None, _ctx(server)), "prompts", "greet")["_meta"] == UI_META


def test_a_prompt_without_meta_emits_no_meta_key() -> None:
    server = _server()
    server.register_prompt(name="greet", render=lambda **_: "hi")
    assert "_meta" not in _entry(handle_prompts_list(None, _ctx(server)), "prompts", "greet")


# ---------- decorator forms ----------


def test_service_tool_decorator_accepts_meta() -> None:
    server = _server()

    @server.service_tool(name="d.create", meta=UI_META)
    def create(**_: Any) -> dict[str, Any]:
        return {}

    assert server.tools.get("d.create").meta == UI_META


def test_selector_tool_decorator_accepts_meta() -> None:
    server = _server()

    @server.selector_tool(name="d.list", kind=SelectorKind.LIST, meta=UI_META)
    def listing(**_: Any) -> list[Any]:
        return []

    assert server.tools.get("d.list").meta == UI_META


def test_resource_decorator_accepts_meta() -> None:
    server = _server()

    @server.resource(uri_template="d://{pk}", kind=SelectorKind.RETRIEVE, meta=UI_META)
    def thing(*, pk: str) -> dict[str, Any]:
        return {"pk": pk}

    entry = _entry(
        handle_resources_templates_list(None, _ctx(server)), "resourceTemplates", "thing"
    )
    assert entry["_meta"] == UI_META


def test_prompt_decorator_accepts_meta() -> None:
    server = _server()

    @server.prompt(name="d.greet", meta=UI_META)
    def greet(**_: Any) -> str:
        return "hi"

    assert _entry(handle_prompts_list(None, _ctx(server)), "prompts", "d.greet")["_meta"] == UI_META


# ---------- declarative bulk registration ----------


def test_tool_definition_carries_meta_and_defaults_fill_it_in() -> None:
    """``register_tools`` needs no change — it forwards every non-``None`` field."""
    server = _server()
    bindings = register_tools(
        server,
        definitions=[
            ToolDefinition.selector(
                name="bulk.own",
                spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
                meta=UI_META,
            ),
            ToolDefinition.selector(
                name="bulk.inherited",
                spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
            ),
        ],
        selector_defaults=SelectorDefaults(meta={"example.com/panel": {"href": "panel://default"}}),
    )
    assert bindings[0].meta == UI_META
    assert bindings[1].meta == {"example.com/panel": {"href": "panel://default"}}


def test_service_tool_definition_carries_meta() -> None:
    server = _server()
    (binding,) = register_tools(
        server,
        definitions=[
            ToolDefinition.service(
                name="bulk.create",
                spec=ServiceSpec(service=lambda **_: {}, atomic=False),
                meta=UI_META,
            )
        ],
    )
    assert binding.meta == UI_META


# ---------- isolation ----------


def test_each_binding_gets_its_own_meta_dict() -> None:
    """No shared mutable default, and the caller's dict is not aliased."""
    server = _server()
    passed: dict[str, Any] = {"a": 1}
    first = server.register_service_tool(
        name="iso.one", spec=ServiceSpec(service=lambda **_: {}, atomic=False), meta=passed
    )
    second = server.register_service_tool(
        name="iso.two", spec=ServiceSpec(service=lambda **_: {}, atomic=False)
    )
    assert first.meta is not passed
    assert second.meta == {}
    first.meta["b"] = 2
    assert passed == {"a": 1}
    assert second.meta == {}


def test_binding_meta_is_copied_before_it_reaches_the_wire() -> None:
    """A caller mutating the emitted payload must not corrupt the binding."""
    server = _server()
    binding = server.register_service_tool(
        name="copy.me", spec=ServiceSpec(service=lambda **_: {}, atomic=False), meta={"a": 1}
    )
    entry = _entry(handle_tools_list(None, _ctx(server)), "tools", "copy.me")
    entry["_meta"]["a"] = 99
    assert binding.meta == {"a": 1}


def test_meta_is_passed_through_verbatim_without_key_validation() -> None:
    """No key is reserved, rejected, or rewritten at this layer."""
    server = _server()
    weird: dict[str, Any] = {
        "": None,
        "modelcontextprotocol.io/x": [1, 2],
        "9": {"deep": {"er": 1}},
    }
    server.register_resource(
        name="thing",
        uri_template="things://{pk}",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: {"pk": pk}),
        meta=weird,
    )
    out: Any = handle_resources_read({"uri": "things://7"}, _ctx(server))
    assert out["contents"][0]["_meta"] == weird
