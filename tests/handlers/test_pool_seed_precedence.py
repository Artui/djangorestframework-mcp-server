"""Client-supplied names never outrank the transport's own pool seeds.

Every method that lets a caller route a value into a kwarg pool — a prompt
argument, a URI-template variable, a completion sibling, a chain step's
``inputs`` — has to apply ``RESERVED_POOL_SEEDS`` precedence, or an argument
called ``user`` binds to the parameter the dispatcher owns and the callable runs
under a principal the caller named.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer, PromptArgument
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_completion_complete import handle_completion_complete
from rest_framework_mcp.handlers.handle_prompts_get import handle_prompts_get
from rest_framework_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

_CALLER = "alice"


class _AlwaysAllow:
    """Minimal MCP permission — a registration must declare one."""

    def has_permission(self, *_args: object, **_kwargs: object) -> bool:
        return True


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer, *, resources: ResourceRegistry | None = None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=_CALLER),
        tools=server.tools,
        resources=resources if resources is not None else server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


# ---------- prompts/get ----------


def _server_with_scoped_prompt() -> MCPServer:
    server = _server()
    server.register_prompt(
        name="scoped",
        render=lambda *, user, topic: f"{user}:{topic}",
        arguments=[PromptArgument(name="topic", required=True)],
        permissions=[_AlwaysAllow()],
    )
    return server


def _rendered(out: Any) -> str:
    assert isinstance(out, dict), out
    return out["messages"][0]["content"]["text"]


def test_prompt_argument_named_user_cannot_replace_the_caller() -> None:
    """The documented ``def render(user, topic)`` shape: ``user`` is the
    authenticated principal, never an argument the client chose."""
    server = _server_with_scoped_prompt()

    out = handle_prompts_get(
        {"name": "scoped", "arguments": {"topic": "x", "user": "victim"}}, _ctx(server)
    )

    assert _rendered(out) == f"{_CALLER}:x"


def test_prompt_argument_named_request_cannot_replace_the_request() -> None:
    """The same key works for ``request``, which is what a ``render`` reading
    ``request.user`` would otherwise be fooled by."""
    seen: dict[str, Any] = {}

    server = _server()

    def render(*, request: Any, topic: str) -> str:
        seen["user"] = request.user
        return topic

    server.register_prompt(
        name="scoped",
        render=render,
        arguments=[PromptArgument(name="topic", required=True)],
        permissions=[_AlwaysAllow()],
    )

    handle_prompts_get(
        {"name": "scoped", "arguments": {"topic": "x", "request": "spoofed"}}, _ctx(server)
    )

    assert seen["user"] == _CALLER


async def test_async_prompt_argument_named_user_cannot_replace_the_caller() -> None:
    server = _server_with_scoped_prompt()

    out = await handle_prompts_get_async(
        {"name": "scoped", "arguments": {"topic": "x", "user": "victim"}}, _ctx(server)
    )

    assert _rendered(out) == f"{_CALLER}:x"


def test_ordinary_prompt_arguments_still_reach_render() -> None:
    """The precedence must not cost a prompt its own arguments."""
    server = _server_with_scoped_prompt()

    out = handle_prompts_get({"name": "scoped", "arguments": {"topic": "weekly"}}, _ctx(server))

    assert _rendered(out) == f"{_CALLER}:weekly"


# ---------- resources/read ----------


def _registry_with_templated_resource(variable: str) -> ResourceRegistry:
    """Register the binding straight onto the registry.

    ``register_resource`` refuses a template variable named after a reserved
    seed, so the runtime precedence is reached only by bypassing it — which is
    exactly the second lock this asserts.
    """
    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="notes",
            uri_template=f"notes://{{{variable}}}/{{pk}}",
            description=None,
            selector=lambda user, pk: {"scoped_to": user, "pk": pk},
            kind=SelectorKind.RETRIEVE,
        )
    )
    return resources


def test_uri_template_variable_named_user_cannot_replace_the_caller() -> None:
    resources = _registry_with_templated_resource("user")

    out = handle_resources_read({"uri": "notes://victim/1"}, _ctx(_server(), resources=resources))

    assert isinstance(out, dict)
    assert f'"scoped_to": "{_CALLER}"' in out["contents"][0]["text"]


async def test_async_uri_template_variable_named_user_cannot_replace_the_caller() -> None:
    resources = _registry_with_templated_resource("user")

    out = await handle_resources_read_async(
        {"uri": "notes://victim/1"}, _ctx(_server(), resources=resources)
    )

    assert isinstance(out, dict)
    assert f'"scoped_to": "{_CALLER}"' in out["contents"][0]["text"]


def test_ordinary_uri_template_variables_still_reach_the_selector() -> None:
    resources = _registry_with_templated_resource("user")

    out = handle_resources_read({"uri": "notes://victim/42"}, _ctx(_server(), resources=resources))

    assert isinstance(out, dict)
    assert '"pk": "42"' in out["contents"][0]["text"]


# ---------- completion/complete ----------


def _server_with_completer(completer: Any) -> MCPServer:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda **_: "x",
        arguments=[PromptArgument(name="id"), PromptArgument(name="other")],
        completions={"id": completer},
        permissions=[_AlwaysAllow()],
    )
    return server


def _complete(server: MCPServer, siblings: dict[str, Any]) -> Any:
    return handle_completion_complete(
        {
            "ref": {"type": "ref/prompt", "name": "p"},
            "argument": {"name": "id", "value": ""},
            "context": {"arguments": siblings},
        },
        _ctx(server),
    )


def test_a_sibling_named_instance_does_not_reach_the_completer() -> None:
    """``instance`` is one of the three reserved seeds this handler never
    supplies, so re-seeding a hand-picked few left it reachable."""
    seen: dict[str, Any] = {}

    def completer(*, value: str, **kwargs: Any) -> list[str]:
        seen.update(kwargs)
        return ["a"]

    out = _complete(_server_with_completer(completer), {"instance": {"attacker": True}})

    assert isinstance(out, dict)
    assert "instance" not in seen


def test_a_sibling_named_data_does_not_reach_the_completer() -> None:
    seen: dict[str, Any] = {}

    def completer(*, value: str, data: Any = "seed-untouched") -> list[str]:
        seen["data"] = data
        return ["a"]

    _complete(_server_with_completer(completer), {"data": "attacker"})

    assert seen["data"] == "seed-untouched"


def test_an_ordinary_sibling_still_reaches_the_completer_by_name() -> None:
    """Stripping is scoped to the reserved names; a real sibling still binds."""
    seen: dict[str, Any] = {}

    def completer(*, value: str, other: str) -> list[str]:
        seen["other"] = other
        return ["a"]

    _complete(_server_with_completer(completer), {"other": "kept"})

    assert seen["other"] == "kept"


def test_the_arguments_seed_still_carries_the_clients_whole_mapping() -> None:
    """``arguments`` is documented as the siblings the client resolved, and a
    completer reading it knows it is reading caller input."""
    seen: dict[str, Any] = {}

    def completer(*, value: str, arguments: dict[str, Any]) -> list[str]:
        seen.update(arguments)
        return ["a"]

    _complete(_server_with_completer(completer), {"instance": "raw", "other": "kept"})

    assert seen == {"instance": "raw", "other": "kept"}


# ---------- chain steps ----------


@pytest.mark.django_db
def test_chain_step_inputs_forwarding_client_args_cannot_replace_the_caller() -> None:
    """``inputs=lambda ctx: dict(ctx.args)`` is the natural way to hand a chain
    tool's arguments to a step, and it forwards whatever the client sent."""
    seen: dict[str, Any] = {}

    def fetch(*, user: Any, pk: int) -> dict[str, Any]:
        seen["user"] = user
        return {"pk": pk}

    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                alias="row",
                spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=fetch),
                inputs=lambda ctx: dict(ctx.args),
            )
        ],
    )

    handle_tools_call({"name": "chain", "arguments": {"pk": 3, "user": 7}}, _ctx(server))

    assert seen["user"] == _CALLER


@pytest.mark.django_db
def test_chain_step_inputs_still_owns_the_data_seed() -> None:
    """Only the identity seeds are reclaimed. ``data`` / ``instance`` stay the
    ``inputs`` callable's to set — that is how one step feeds the next."""
    seen: dict[str, Any] = {}

    def service(*, data: Any) -> dict[str, Any]:
        seen["data"] = data
        return {"ok": True}

    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                alias="write",
                spec=ServiceSpec(service=service, atomic=False),
                inputs=lambda ctx: {"data": {"from": "inputs"}},
            )
        ],
    )

    handle_tools_call({"name": "chain", "arguments": {}}, _ctx(server))

    assert seen["data"] == {"from": "inputs"}
