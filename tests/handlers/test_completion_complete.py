"""``completion/complete`` — argument autocompletion for prompts and templates."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer, PromptArgument
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.permissions.types.mcp_permission import MCPPermission
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_completion_complete import handle_completion_complete
from rest_framework_mcp.handlers.handle_initialize import handle_initialize
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

LANGUAGES = ["python", "pytorch", "pyside", "ruby"]


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer, scopes: tuple[str, ...] = ()) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None, scopes=scopes),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=server.config.protocol_versions[0],
        config=server.config,
    )


def _prompt_server(**kwargs: Any) -> MCPServer:
    server = _server()
    server.register_prompt(
        name="code_review",
        render=lambda language="python": f"Review some {language}",
        arguments=[PromptArgument(name="language"), PromptArgument(name="framework")],
        completions={"language": lambda value: [x for x in LANGUAGES if x.startswith(value)]},
        **kwargs,
    )
    return server


def _complete(server: MCPServer, params: dict[str, Any], **ctx: Any) -> Any:
    return handle_completion_complete(params, _ctx(server, **ctx))


# ----- ref/prompt -----


def test_prompt_argument_completion() -> None:
    result = _complete(
        _prompt_server(),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
        },
    )
    assert result == {"completion": {"values": ["python", "pytorch", "pyside"], "hasMore": False}}


def test_an_empty_value_is_valid_and_matches_everything() -> None:
    """A client asks the moment the field is focused, before anything is typed."""
    result = _complete(
        _prompt_server(),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language"},
        },
    )
    assert result["completion"]["values"] == LANGUAGES


def test_context_arguments_reach_the_completer_by_name() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="language"), PromptArgument(name="framework")],
        completions={"framework": lambda language: [f"{language}-web"]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/prompt", "name": "p"},
            "argument": {"name": "framework", "value": "fla"},
            "context": {"arguments": {"language": "python"}},
        },
    )
    assert result["completion"]["values"] == ["python-web"]


def test_a_completer_may_declare_the_whole_arguments_mapping() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": lambda arguments, value: [f"{sorted(arguments)}:{value}"]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/prompt", "name": "p"},
            "argument": {"name": "a", "value": "v"},
            "context": {"arguments": {"z": 1}},
        },
    )
    assert result["completion"]["values"] == ["['z']:v"]


def test_transport_seeds_win_over_a_same_named_sibling_argument() -> None:
    """A client-supplied ``value`` sibling must not shadow the real one."""
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": lambda value: [value]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/prompt", "name": "p"},
            "argument": {"name": "a", "value": "real"},
            "context": {"arguments": {"value": "spoofed"}},
        },
    )
    assert result["completion"]["values"] == ["real"]


# ----- ref/resource -----


def test_resource_template_variable_completion() -> None:
    server = _server()
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: None),
        completions={"pk": lambda: ["1", "2", "3"]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/resource", "uri": "invoices://{pk}"},
            "argument": {"name": "pk", "value": ""},
        },
    )
    assert result["completion"]["values"] == ["1", "2", "3"]


def test_a_concrete_uri_is_not_mistaken_for_its_own_template() -> None:
    """``resolve`` would match ``invoices://7`` — the ref lookup must not."""
    server = _server()
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: None),
        completions={"pk": lambda: ["1"]},
    )
    result = _complete(
        server,
        {
            "ref": {"type": "ref/resource", "uri": "invoices://7"},
            "argument": {"name": "pk", "value": ""},
        },
    )
    assert isinstance(result, JsonRpcError)
    assert result.code == -32602


# ----- capping -----


def test_values_are_capped_at_the_spec_limit_with_has_more() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": lambda: (str(i) for i in range(500))},
    )
    result = _complete(
        server, {"ref": {"type": "ref/prompt", "name": "p"}, "argument": {"name": "a"}}
    )
    assert len(result["completion"]["values"]) == 100
    assert result["completion"]["hasMore"] is True
    # ``total`` is optional and deliberately unset — see ``Completion``.
    assert "total" not in result["completion"]


def test_the_generator_is_sliced_not_drained() -> None:
    """101 items are pulled to decide ``hasMore``; the rest are never produced."""
    produced: list[int] = []

    def counting() -> Any:
        for i in range(10_000):
            produced.append(i)
            yield str(i)

    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": counting},
    )
    _complete(server, {"ref": {"type": "ref/prompt", "name": "p"}, "argument": {"name": "a"}})
    assert len(produced) == 101


def test_a_lone_string_is_one_suggestion_not_its_characters() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda: "x",
        arguments=[PromptArgument(name="a")],
        completions={"a": lambda: "python"},
    )
    result = _complete(
        server, {"ref": {"type": "ref/prompt", "name": "p"}, "argument": {"name": "a"}}
    )
    assert result["completion"]["values"] == ["python"]


# ----- guards -----


class _DenyAll(MCPPermission):
    def has_permission(self, request: Any, token: Any) -> bool:
        return False

    def required_scopes(self) -> tuple[str, ...]:
        return ()


def test_completion_runs_the_bindings_permissions() -> None:
    """Otherwise a gated resource still answers "which ids exist?" one key at a time."""
    result = _complete(
        _prompt_server(permissions=[_DenyAll()]),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
        },
    )
    assert isinstance(result, JsonRpcError)
    assert result.code == -32006


class _AlwaysLimited:
    def consume(self, request: Any, token: Any) -> int | None:
        return 30


def test_completion_runs_the_bindings_rate_limits() -> None:
    """The completion spec asks for this explicitly — typing is a request per key."""
    result = _complete(
        _prompt_server(rate_limits=[_AlwaysLimited()]),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
        },
    )
    assert isinstance(result, JsonRpcError)
    assert result.code == -32005
    assert result.data == {"retryAfter": 30}


def test_unknown_prompt_is_invalid_params() -> None:
    result = _complete(
        _prompt_server(),
        {"ref": {"type": "ref/prompt", "name": "nope"}, "argument": {"name": "a"}},
    )
    assert isinstance(result, JsonRpcError) and result.code == -32602


def test_unknown_resource_is_invalid_params() -> None:
    result = _complete(
        _server(),
        {"ref": {"type": "ref/resource", "uri": "nope://{x}"}, "argument": {"name": "x"}},
    )
    assert isinstance(result, JsonRpcError) and result.code == -32602


def test_an_argument_with_no_completer_lists_what_is_completable() -> None:
    result = _complete(
        _prompt_server(),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "framework", "value": "fl"},
        },
    )
    assert isinstance(result, JsonRpcError)
    assert result.code == -32602
    assert result.data == {"completable": ["language"]}


@pytest.mark.parametrize(
    "params",
    [
        None,
        {"argument": {"name": "a"}},
        {"ref": "not-an-object", "argument": {"name": "a"}},
        {"ref": {"type": "ref/prompt", "name": "code_review"}},
        {"ref": {"type": "ref/prompt", "name": "code_review"}, "argument": {}},
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": 7},
        },
        {"ref": {"type": "ref/prompt"}, "argument": {"name": "a"}},
        {"ref": {"type": "ref/resource"}, "argument": {"name": "a"}},
        {"ref": {"type": "ref/nonsense"}, "argument": {"name": "a"}},
    ],
)
def test_malformed_requests_are_invalid_params(params: Any) -> None:
    result = _complete(_prompt_server(), params)
    assert isinstance(result, JsonRpcError) and result.code == -32602


def test_a_non_dict_context_is_ignored_rather_than_rejected() -> None:
    """``context`` is optional; a junk value degrades to "no siblings"."""
    result = _complete(
        _prompt_server(),
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
            "context": {"arguments": "not-a-mapping"},
        },
    )
    assert result["completion"]["values"] == ["python", "pytorch", "pyside"]


# ----- registration and advertisement -----


def test_a_completer_for_an_unknown_prompt_argument_is_refused() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match=r"\['langauge'\]"):
        server.register_prompt(
            name="p",
            render=lambda: "x",
            arguments=[PromptArgument(name="language")],
            completions={"langauge": lambda: []},
        )


def test_a_completer_for_an_unknown_template_variable_is_refused() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match=r"\['id'\]"):
        server.register_resource(
            name="invoice",
            uri_template="invoices://{pk}",
            selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: None),
            completions={"id": lambda: []},
        )


def test_the_capability_is_advertised_only_when_something_is_completable() -> None:
    bare = handle_initialize({}, _ctx(_server()))
    assert "completions" not in bare.capabilities.to_dict()

    with_completer = handle_initialize({}, _ctx(_prompt_server()))
    assert with_completer.capabilities.to_dict()["completions"] == {}


def test_a_resource_completer_alone_advertises_the_capability() -> None:
    server = _server()
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: None),
        completions={"pk": lambda: []},
    )
    assert handle_initialize({}, _ctx(server)).capabilities.to_dict()["completions"] == {}


@pytest.mark.django_db(transaction=True)
async def test_async_dispatch_routes_completion_through_the_executor() -> None:
    """A completer reading the ORM must not run on the event loop."""
    from rest_framework_mcp.handlers.async_dispatch import adispatch

    result = await adispatch(
        "completion/complete",
        {
            "ref": {"type": "ref/prompt", "name": "code_review"},
            "argument": {"name": "language", "value": "py"},
        },
        _ctx(_prompt_server()),
    )
    assert result["completion"]["values"] == ["python", "pytorch", "pyside"]


def test_capabilities_describe_what_the_server_can_answer() -> None:
    """One rule for all four — nothing is advertised that would return nothing."""
    bare = handle_initialize({}, _ctx(_server())).capabilities.to_dict()
    assert bare == {}

    server = _server()
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: None),
    )
    assert handle_initialize({}, _ctx(server)).capabilities.to_dict() == {"resources": {}}
