"""``server/discover`` plus the cacheability fields every result now carries."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, ResourceEncoding
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.handlers.dispatch import dispatch
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server(**config: Any) -> MCPServer:
    return MCPServer(
        name="t",
        version="1.2.3",
        description="Use this to bill people.",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(**config) if config else None,
    )


def _ctx(server: MCPServer) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=server.config.protocol_versions[0],
        server_info=server._server_info,
        instructions=server.description,
        config=server.config,
    )


def _with_a_tool(server: MCPServer) -> MCPServer:
    server.register_service_tool(
        name="t.svc",
        description="does a thing",
        spec=ServiceSpec(service=lambda **_: {}, atomic=False),
    )
    return server


# ----- server/discover -----


def test_discover_reports_versions_capabilities_and_identity() -> None:
    server = _with_a_tool(_server())
    result = dispatch("server/discover", None, _ctx(server))
    assert result["supportedVersions"] == list(server.config.protocol_versions)
    assert result["capabilities"] == {"tools": {}}
    assert result["instructions"] == "Use this to bill people."


def test_discover_puts_server_info_in_meta_not_at_the_top_level() -> None:
    """The spec moved it there because it is self-reported and unverified."""
    result = dispatch("server/discover", None, _ctx(_server()))
    assert "serverInfo" not in result
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"] == {
        "name": "t",
        "version": "1.2.3",
    }


def test_discover_reports_the_same_capabilities_as_initialize() -> None:
    """Two ways in, one answer — the bundle is a property of the server."""
    server = _with_a_tool(_server())
    ctx = _ctx(server)
    discovered = dispatch("server/discover", None, ctx)["capabilities"]
    initialized = dispatch("initialize", {}, ctx)["capabilities"]
    assert discovered == initialized


def test_discover_ignores_params() -> None:
    """The request carries nothing beyond ``_meta``, which is the transport's."""
    server = _server()
    assert dispatch("server/discover", {"nonsense": 1}, _ctx(server)) == dispatch(
        "server/discover", None, _ctx(server)
    )


def test_discover_is_public_even_when_listings_are_filtered() -> None:
    """It reports which capabilities exist, not which bindings a caller may see."""
    server = _with_a_tool(_server(filter_listings_by_permissions=True))
    result = dispatch("server/discover", None, _ctx(server))
    assert result["cacheScope"] == "public"


# ----- resultType -----


def test_every_result_carries_the_complete_discriminator() -> None:
    """Stamped in the response envelope, so no handler can forget it."""
    from rest_framework_mcp.protocol.types.json_rpc_response import JsonRpcResponse

    body = JsonRpcResponse(id=1, result={"tools": []}).to_dict()
    assert body["result"]["resultType"] == "complete"


def test_a_non_dict_result_passes_through_untouched() -> None:
    from rest_framework_mcp.protocol.types.json_rpc_response import JsonRpcResponse

    assert JsonRpcResponse(id=1, result=[1, 2]).to_dict()["result"] == [1, 2]


def test_a_handler_supplied_result_type_wins() -> None:
    from rest_framework_mcp.protocol.types.json_rpc_response import JsonRpcResponse

    body = JsonRpcResponse(id=1, result={"resultType": "input_required"}).to_dict()
    assert body["result"]["resultType"] == "input_required"


# ----- ttlMs / cacheScope -----


def test_catalogs_are_public_when_listings_are_not_filtered() -> None:
    server = _with_a_tool(_server(filter_listings_by_permissions=False))
    result = dispatch("tools/list", None, _ctx(server))
    assert result["cacheScope"] == "public"
    assert result["ttlMs"] == 60_000


def test_catalogs_are_private_when_listings_are_filtered() -> None:
    """A filtered listing is a function of the caller, so it must not be shared.

    Labelling it ``public`` would licence a caching proxy to serve one tenant's
    visible tools to another.
    """
    server = _with_a_tool(_server(filter_listings_by_permissions=True))
    for method in ("tools/list", "resources/list", "resources/templates/list", "prompts/list"):
        assert dispatch(method, None, _ctx(server))["cacheScope"] == "private", method


def test_the_catalog_ttl_is_configurable() -> None:
    server = _with_a_tool(_server(catalog_cache_ttl_ms=0))
    assert dispatch("tools/list", None, _ctx(server))["ttlMs"] == 0


def test_a_resource_read_is_always_private_and_uncached_by_default() -> None:
    server = _server()
    server.register_resource(
        name="doc",
        uri_template="docs://readme",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: "hi"),
        mime_type="text/markdown",
        encoding=ResourceEncoding.TEXT,
    )
    result = dispatch("resources/read", {"uri": "docs://readme"}, _ctx(server))
    assert result["cacheScope"] == "private"
    assert result["ttlMs"] == 0


def test_a_static_resource_can_opt_into_a_ttl() -> None:
    """A view is a document that changes on deploy, and hosts prefetch views."""
    server = _server()
    server.register_ui_resource(
        name="table",
        uri="ui://table.html",
        html="<p>hi</p>",
        cache_ttl_ms=3_600_000,
    )
    result = dispatch("resources/read", {"uri": "ui://table.html"}, _ctx(server))
    assert result["ttlMs"] == 3_600_000
    # Still private: caching is about this caller's copy, not about sharing.
    assert result["cacheScope"] == "private"


def test_discover_falls_back_to_the_settings_identity() -> None:
    """The degenerate path: a context assembled without an owning server."""
    server = _server()
    ctx = _ctx(server)
    ctx = MCPCallContext(
        http_request=ctx.http_request,
        token=ctx.token,
        tools=ctx.tools,
        resources=ctx.resources,
        prompts=ctx.prompts,
        protocol_version=ctx.protocol_version,
        server_info=None,
        config=ctx.config,
    )
    info = dispatch("server/discover", None, ctx)["_meta"]["io.modelcontextprotocol/serverInfo"]
    # The test settings configure SERVER_INFO, which is exactly the source
    # this path is meant to reach for.
    assert info["name"].startswith("djangorestframework-mcp-server")
    assert info["name"] != "t"
