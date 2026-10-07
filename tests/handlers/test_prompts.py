from __future__ import annotations

from typing import Any, ClassVar

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest, QueryDict
from rest_framework.permissions import BasePermission

from rest_framework_mcp import DRFPermissionAdapter, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_prompts_get import handle_prompts_get
from rest_framework_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from rest_framework_mcp.handlers.handle_prompts_list import handle_prompts_list
from rest_framework_mcp.handlers.render_prompt_messages import normalize_render_result
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.prompt_argument import PromptArgument
from rest_framework_mcp.protocol.types.prompt_message import PromptMessage
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


# ---------- normalize_render_result ----------


def test_normalize_string_becomes_user_text_message() -> None:
    out = normalize_render_result("hello")
    assert out == [PromptMessage.text(role="user", text="hello")]


def test_normalize_promptmessage_passthrough() -> None:
    msg = PromptMessage.text(role="assistant", text="hi")
    assert normalize_render_result(msg) == [msg]


def test_normalize_list_of_strings() -> None:
    out = normalize_render_result(["a", "b"])
    assert [m.content["text"] for m in out] == ["a", "b"]
    assert all(m.role == "user" for m in out)


def test_normalize_list_of_dicts() -> None:
    out = normalize_render_result([{"role": "system", "content": {"type": "text", "text": "sys"}}])
    assert out[0].role == "system"


def test_normalize_list_of_messages() -> None:
    msg = PromptMessage.text(role="user", text="x")
    assert normalize_render_result([msg]) == [msg]


def test_normalize_unsupported_item_raises() -> None:
    with pytest.raises(TypeError, match="Unsupported"):
        normalize_render_result([123])


def test_normalize_unsupported_root_raises() -> None:
    with pytest.raises(TypeError, match="unsupported value"):
        normalize_render_result(123)


# ---------- prompts/list ----------


def test_prompts_list_advertises_arguments() -> None:
    server = _server()
    server.register_prompt(
        name="greet",
        description="Say hi",
        render=lambda **_: "hi",
        arguments=[PromptArgument(name="who", required=True)],
    )
    out = handle_prompts_list(None, _ctx(server))
    assert isinstance(out, dict)
    prompt = out["prompts"][0]
    assert prompt["name"] == "greet"
    assert prompt["arguments"][0]["required"] is True


def test_prompts_list_paginates(settings) -> None:
    # Replacing the dict drops the suite-wide opt-out in conftest_settings, and
    # this test's throwaway prompts are unguarded; carried through so the
    # subject stays pagination.
    settings.REST_FRAMEWORK_MCP = {"PAGE_SIZE": 2, "REQUIRE_TOOL_PERMISSIONS": False}
    server = _server()
    for i in range(3):
        server.register_prompt(name=f"p{i}", render=lambda **_: "x")
    out = handle_prompts_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert len(out["prompts"]) == 2
    assert "nextCursor" in out


def test_prompts_list_rejects_bad_cursor() -> None:
    server = _server()
    out = handle_prompts_list({"cursor": "###"}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_list_rejects_non_string_cursor() -> None:
    server = _server()
    out = handle_prompts_list({"cursor": 1}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


# ---------- prompts/get sync ----------


def test_prompts_get_renders_string() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "rendered")
    out = handle_prompts_get({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["messages"][0]["content"]["text"] == "rendered"


def test_prompts_get_threads_arguments_to_render() -> None:
    server = _server()
    server.register_prompt(
        name="echo",
        render=lambda *, who: f"hello {who}",
        arguments=[PromptArgument(name="who", required=True)],
    )
    out = handle_prompts_get({"name": "echo", "arguments": {"who": "world"}}, _ctx(server))
    assert isinstance(out, dict)
    assert "hello world" in out["messages"][0]["content"]["text"]


def test_prompts_get_missing_required_arg() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda *, who: who,
        arguments=[PromptArgument(name="who", required=True)],
    )
    out = handle_prompts_get({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602
    assert out.data == {"missing": ["who"]}


def test_prompts_get_unknown_returns_invalid_params() -> None:
    server = _server()
    out = handle_prompts_get({"name": "nope", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_get_rejects_non_dict_params() -> None:
    server = _server()
    out = handle_prompts_get(None, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_get_rejects_missing_name() -> None:
    server = _server()
    out = handle_prompts_get({}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_get_rejects_non_dict_arguments() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "x")
    out = handle_prompts_get({"name": "p", "arguments": [1]}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_get_handles_unsupported_render_value() -> None:
    server = _server()
    server.register_prompt(name="bad", render=lambda **_: 123)
    out = handle_prompts_get({"name": "bad", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32603


class _DenyAll:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return False

    def required_scopes(self) -> list[str]:
        return ["s"]


def test_prompts_get_permission_denied() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "x", permissions=[_DenyAll()])
    out = handle_prompts_get({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32006


def test_decorator_uses_function_name_and_docstring() -> None:
    server = _server()

    @server.prompt()
    def greeting(*, name: str) -> str:
        """Say hello."""
        return f"Hello, {name}!"

    binding = server.prompts.get("greeting")
    assert binding is not None
    assert binding.description == "Say hello."


# ---------- prompts/get async ----------


async def test_async_prompts_get_dispatches_async_render() -> None:
    server = _server()

    async def render(**_: Any) -> str:
        return "async-rendered"

    server.register_prompt(name="p", render=render)
    out = await handle_prompts_get_async({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["messages"][0]["content"]["text"] == "async-rendered"


async def test_async_prompts_get_unknown_returns_invalid_params() -> None:
    server = _server()
    out = await handle_prompts_get_async({"name": "nope", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_rejects_non_dict_params() -> None:
    server = _server()
    out = await handle_prompts_get_async(None, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_rejects_missing_name() -> None:
    server = _server()
    out = await handle_prompts_get_async({}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_rejects_non_dict_arguments() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "x")
    out = await handle_prompts_get_async({"name": "p", "arguments": [1]}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_missing_required_arg() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda *, who: who,
        arguments=[PromptArgument(name="who", required=True)],
    )
    out = await handle_prompts_get_async({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_permission_denied() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "x", permissions=[_DenyAll()])
    out = await handle_prompts_get_async({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32006


async def test_async_prompts_get_handles_unsupported_render_value() -> None:
    server = _server()
    server.register_prompt(name="bad", render=lambda **_: 123)
    out = await handle_prompts_get_async({"name": "bad", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32603


# ---------- Prompt + PromptArgument shapes ----------


def test_prompt_argument_minimal_to_dict() -> None:
    assert PromptArgument(name="x").to_dict() == {"name": "x"}


def test_prompt_argument_full_to_dict() -> None:
    out = PromptArgument(name="x", description="d", required=True).to_dict()
    assert out == {"name": "x", "description": "d", "required": True}


def test_prompt_minimal_to_dict() -> None:
    from rest_framework_mcp.protocol.types.prompt import Prompt

    assert Prompt(name="p").to_dict() == {"name": "p"}


def test_prompt_full_to_dict() -> None:
    from rest_framework_mcp.protocol.types.prompt import Prompt

    p = Prompt(
        name="p",
        title="T",
        description="d",
        arguments=[PromptArgument(name="x", required=True)],
        annotations={"k": 1},
    )
    out = p.to_dict()
    assert out["title"] == "T"
    assert out["description"] == "d"
    assert out["arguments"] == [{"name": "x", "required": True}]
    assert out["annotations"] == {"k": 1}


def test_get_prompt_result_to_dict_minimal() -> None:
    from rest_framework_mcp.protocol.types.get_prompt_result import GetPromptResult

    out = GetPromptResult(messages=[PromptMessage.text("user", "hi")]).to_dict()
    assert "description" not in out
    assert out["messages"][0]["role"] == "user"


def test_get_prompt_result_to_dict_with_description() -> None:
    from rest_framework_mcp.protocol.types.get_prompt_result import GetPromptResult

    out = GetPromptResult(messages=[], description="d").to_dict()
    assert out["description"] == "d"


def test_prompt_message_text_helper() -> None:
    msg = PromptMessage.text(role="assistant", text="hi")
    assert msg.role == "assistant"
    assert msg.content == {"type": "text", "text": "hi"}


class _RefusesDeny(BasePermission):
    """Refuses a call whose arguments name ``deny``; records what each check carried."""

    seen: ClassVar[list[tuple[Any, ...]]]

    def has_permission(self, request: Any, view: Any) -> bool:
        type(self).seen.append((dict(request.data), view.action, dict(request.query_params)))
        return "deny" not in request.data


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_prompt_permission_reads_the_prompts_arguments(is_async: bool) -> None:
    # Judged against ``{}``, a permission written over the prompt's arguments
    # admitted every call: the arguments never reached ``request.data``.
    class _Recording(_RefusesDeny):
        seen = []

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_prompt(
        name="p", render=lambda **_: "hello", permissions=[DRFPermissionAdapter(_Recording)]
    )
    # The endpoint's own query string, which no prompt check reads.
    http_request = HttpRequest()
    http_request.GET = QueryDict("deny=1")
    context = MCPCallContext(
        http_request=http_request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )

    async def _get(arguments: dict[str, Any]) -> Any:
        params: dict[str, Any] = {"name": "p", "arguments": arguments}
        if is_async:
            return await handle_prompts_get_async(params, context)
        return await sync_to_async(handle_prompts_get)(params, context)

    denied = await _get({"deny": "1"})
    admitted = await _get({"note": "x"})

    assert isinstance(denied, JsonRpcError) and denied.code == JsonRpcErrorCode.FORBIDDEN
    assert not isinstance(admitted, JsonRpcError), admitted
    assert _Recording.seen == [({"deny": "1"}, "p", {}), ({"note": "x"}, "p", {})]
