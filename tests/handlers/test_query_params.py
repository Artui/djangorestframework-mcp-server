"""Request-level query params: the declared channel, and closing the undeclared one.

MCP requests carry no query string — a ``tools/call`` is a JSON-RPC body and the
transport URL is an endpoint, not a resource locator. So the only correct source
for ``request.query_params`` off-HTTP is a *declared* per-call channel.

Until now there was no declared one **and** an accidental one: every call site
wraps the real Django POST, so whatever query string a client hung off the
endpoint URL showed up in ``request.query_params`` — connection-scoped,
client-controlled, undeclared, invisible to the model. Both halves are fixed by
the same change, because ``build_offline_context(query_params=…)`` *replaces* the
wrapped request's ``GET``.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, QueryDict
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, QueryParam, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_prompts_get import handle_prompts_get
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import split_query_params
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

# ---------- helpers ----------


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _http_request(query_string: str = "") -> HttpRequest:
    """A real Django request, optionally with a query string on the endpoint URL."""
    request = HttpRequest()
    if query_string:
        request.GET = QueryDict(query_string)
    return request


def _ctx(server: MCPServer, http_request: HttpRequest | None = None) -> MCPCallContext:
    return MCPCallContext(
        http_request=http_request if http_request is not None else HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _echo_query(*, request: Any) -> dict[str, Any]:
    """A spec callable that reports exactly what reached ``request.query_params``."""
    return {"seen": dict(request.query_params.items())}


def _register_service(server: MCPServer, **kwargs: Any) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return server.register_service_tool(
            name="echo",
            description="d",
            spec=ServiceSpec(service=_echo_query, atomic=False),
            **kwargs,
        )


def _register_selector(server: MCPServer, **kwargs: Any) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return server.register_selector_tool(
            name="echo.list",
            description="d",
            spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_echo_query),
            **kwargs,
        )


def _call(server: MCPServer, name: str, arguments: dict[str, Any], ctx: Any) -> Any:
    return handle_tools_call({"name": name, "arguments": arguments}, ctx)


# ---------- the declared channel ----------


def test_registered_query_param_reaches_request_query_params() -> None:
    server = _server()
    _register_service(server, query_params=(QueryParam("fields"),))
    out = _call(server, "echo", {"fields": "id,name"}, _ctx(server))
    assert out["structuredContent"] == {"seen": {"fields": "id,name"}}


def test_a_query_param_never_reaches_the_spec_as_an_input() -> None:
    """It is popped from the arguments — one value, one channel."""

    def _service(*, request: Any, **kwargs: Any) -> dict[str, Any]:
        return {"query": dict(request.query_params.items()), "kwargs": sorted(kwargs)}

    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_service_tool(
            name="probe",
            description="d",
            spec=ServiceSpec(service=_service, atomic=False),
            query_params=(QueryParam("fields"),),
        )
    out = _call(server, "probe", {"fields": "id"}, _ctx(server))
    payload = out["structuredContent"]
    assert payload["query"] == {"fields": "id"}
    assert "fields" not in payload["kwargs"]


def test_default_is_seeded_when_the_model_omits_the_argument() -> None:
    server = _server()
    _register_service(server, query_params=(QueryParam("fields", default="id"),))
    out = _call(server, "echo", {}, _ctx(server))
    assert out["structuredContent"] == {"seen": {"fields": "id"}}


def test_an_undeclared_param_is_absent_rather_than_empty() -> None:
    server = _server()
    _register_service(server, query_params=(QueryParam("fields"),))
    out = _call(server, "echo", {}, _ctx(server))
    assert out["structuredContent"] == {"seen": {}}


def test_values_are_stringified_as_on_http() -> None:
    server = _server()
    _register_service(server, query_params=(QueryParam("page_size", type="integer"),))
    out = _call(server, "echo", {"page_size": 25}, _ctx(server))
    assert out["structuredContent"] == {"seen": {"page_size": "25"}}


async def test_the_async_path_routes_query_params_identically() -> None:
    server = _server()
    _register_service(server, query_params=(QueryParam("fields"),))
    out = await handle_tools_call_async(
        {"name": "echo", "arguments": {"fields": "id"}}, _ctx(server)
    )
    assert out["structuredContent"] == {"seen": {"fields": "id"}}


def test_selector_tools_take_the_same_channel() -> None:
    server = _server()
    _register_selector(server, query_params=(QueryParam("fields"),))
    out = _call(server, "echo.list", {"fields": "id"}, _ctx(server))
    assert out["structuredContent"] == {"seen": {"fields": "id"}}


def test_a_selector_query_param_is_not_an_unknown_argument() -> None:
    """``unknown_arguments=REJECT`` must not flag a declared read-shaping arg."""
    server = _server()
    _register_selector(server, query_params=(QueryParam("fields"),))
    out = _call(server, "echo.list", {"fields": "id"}, _ctx(server))
    # A rejection would have come back as a JSON-RPC -32602 instead.
    assert out["structuredContent"] == {"seen": {"fields": "id"}}


# ---------- advertised schema ----------


def test_query_params_are_advertised_on_both_tool_kinds() -> None:
    server = _server()
    _register_service(server, query_params=(QueryParam("fields", description="Sparse fieldset"),))
    _register_selector(server, query_params=(QueryParam("expand", type="boolean"),))
    out = handle_tools_list(None, _ctx(server))
    schemas = {tool["name"]: tool["inputSchema"] for tool in out["tools"]}
    assert schemas["echo"]["properties"]["fields"] == {
        "type": "string",
        "description": "Sparse fieldset",
    }
    assert schemas["echo.list"]["properties"]["expand"] == {"type": "boolean"}


def test_a_query_param_is_never_required() -> None:
    """A read-shaping param the spec runs fine without cannot be required."""
    server = _server()
    _register_service(
        server,
        query_params=(QueryParam("fields", default="id"),),
        url_kwargs=(UrlKwarg("tenant", required=True),),
    )
    out = handle_tools_list(None, _ctx(server))
    schema = out["tools"][0]["inputSchema"]
    assert schema["required"] == ["tenant"]
    assert "fields" in schema["properties"]


# ---------- closing the undeclared channel ----------


def test_the_endpoint_query_string_no_longer_reaches_a_service_tool() -> None:
    """The MCP endpoint's own query string is not a per-call channel."""
    server = _server()
    _register_service(server)
    out = _call(server, "echo", {}, _ctx(server, _http_request("evil=1&fields=all")))
    assert out["structuredContent"] == {"seen": {}}


def test_the_endpoint_query_string_no_longer_reaches_a_selector_tool() -> None:
    server = _server()
    _register_selector(server)
    out = _call(server, "echo.list", {}, _ctx(server, _http_request("evil=1")))
    assert out["structuredContent"] == {"seen": {}}


def test_a_declared_param_wins_over_the_endpoint_query_string() -> None:
    server = _server()
    _register_service(server, query_params=(QueryParam("fields"),))
    out = _call(server, "echo", {"fields": "id"}, _ctx(server, _http_request("fields=everything")))
    assert out["structuredContent"] == {"seen": {"fields": "id"}}


def test_the_endpoint_query_string_no_longer_reaches_a_resource() -> None:
    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_resource(
            name="echo",
            uri_template="echo://all",
            selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_echo_query),
        )
    out = handle_resources_read({"uri": "echo://all"}, _ctx(server, _http_request("evil=1")))
    assert '"seen": {}' in out["contents"][0]["text"]


def test_the_endpoint_query_string_no_longer_reaches_a_prompt() -> None:
    server = _server()

    def _render(*, request: Any) -> str:
        return f"seen={dict(request.query_params.items())}"

    server.register_prompt(name="p", description="d", render=_render)
    out = handle_prompts_get({"name": "p"}, _ctx(server, _http_request("evil=1")))
    assert "seen={}" in out["messages"][0]["content"]["text"]


@pytest.mark.django_db
def test_the_endpoint_query_string_no_longer_reaches_a_chain_step() -> None:
    # A chain wraps its steps in ``transaction.atomic()`` by default.
    from rest_framework_mcp.registry.types.chain_step import ChainStep

    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_chain_tool(
            name="chain",
            description="d",
            steps=[ChainStep("only", ServiceSpec(service=_echo_query, atomic=False))],
        )
    out = _call(server, "chain", {}, _ctx(server, _http_request("evil=1")))
    # Single-step chain renders that step's result directly.
    assert out["structuredContent"] == {"seen": {}}


# ---------- registration-time validation ----------


def test_a_reserved_name_is_rejected() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="query_params name"):
        _register_service(server, query_params=(QueryParam("page"),))


def test_a_duplicate_name_is_rejected() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="duplicate query_params"):
        _register_service(server, query_params=(QueryParam("fields"), QueryParam("fields")))


def test_one_name_cannot_be_both_a_query_param_and_a_url_kwarg() -> None:
    """A value routes to one channel and is popped from the arguments once."""
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="both a QueryParam and a UrlKwarg"):
        _register_service(
            server, query_params=(QueryParam("tenant"),), url_kwargs=(UrlKwarg("tenant"),)
        )


def test_the_exclusivity_rule_applies_to_selector_tools_too() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="both a QueryParam and a UrlKwarg"):
        _register_selector(
            server, query_params=(QueryParam("tenant"),), url_kwargs=(UrlKwarg("tenant"),)
        )


# ---------- the split helper ----------


def test_split_returns_the_original_mapping_when_nothing_is_declared() -> None:
    arguments = {"a": 1}
    params, values = split_query_params(arguments, ())
    assert params is arguments
    assert values == {}


def test_split_pops_declared_names_and_leaves_the_rest() -> None:
    params, values = split_query_params(
        {"fields": "id", "other": 2},
        (
            QueryParam("fields"),
            QueryParam("absent"),
        ),
    )
    assert params == {"other": 2}
    assert values == {"fields": "id"}
