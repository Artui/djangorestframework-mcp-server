"""An interactive view over the real ``resources/*`` handlers, both transports."""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import UI_META_KEY, UI_RESOURCE_MIME_TYPE, JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_resources_list import handle_resources_list
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.types.ui_csp import UICsp
from rest_framework_mcp.registry.types.ui_resource_meta import UIResourceMeta
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

VIEW = "<!doctype html><h1>Invoices</h1>"
URI = "ui://invoices/table.html"


def _server(**overrides: Any) -> MCPServer:
    server = MCPServer(
        name="test",
        description="d",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    server.register_ui_resource(
        **{
            "name": "invoices_table",
            "uri": URI,
            "html": VIEW,
            "description": "Invoices, as a table.",
            **overrides,
        }
    )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _contents(response: Any) -> dict[str, Any]:
    return response["contents"][0]


class TestRead:
    def test_returns_the_document_not_a_json_string(self) -> None:
        out = handle_resources_read({"uri": URI}, _ctx(_server()))
        assert _contents(out)["text"] == VIEW

    async def test_the_async_transport_returns_the_same_body(self) -> None:
        """The two read handlers are parallel implementations, so the shared
        builder is the only thing keeping their wire shapes identical."""
        sync = handle_resources_read({"uri": URI}, _ctx(_server()))
        async_ = await handle_resources_read_async({"uri": URI}, _ctx(_server()))

        assert _contents(async_) == _contents(sync)

    def test_advertises_the_apps_mime_type(self) -> None:
        out = handle_resources_read({"uri": URI}, _ctx(_server()))
        assert _contents(out)["mimeType"] == UI_RESOURCE_MIME_TYPE

    def test_carries_the_ui_metadata_on_the_contents_block(self) -> None:
        server = _server(ui=UIResourceMeta(csp=UICsp(connect_domains=("https://api.x",))))
        out = handle_resources_read({"uri": URI}, _ctx(server))

        assert _contents(out)["_meta"] == {
            UI_META_KEY: {"csp": {"connectDomains": ["https://api.x"]}}
        }

    def test_a_view_whose_source_returns_a_non_string_is_a_clean_error(self) -> None:
        """Not a transport-level 500 — the client gets a JSON-RPC error."""
        server = _server(html=None, selector=lambda: {"oops": True})
        out = handle_resources_read({"uri": URI}, _ctx(server))

        assert out.code == JsonRpcErrorCode.INTERNAL_ERROR

    async def test_the_async_transport_reports_that_error_too(self) -> None:
        server = _server(html=None, selector=lambda: {"oops": True})
        out = await handle_resources_read_async({"uri": URI}, _ctx(server))

        assert out.code == JsonRpcErrorCode.INTERNAL_ERROR


class TestListing:
    def test_appears_in_resources_list_beside_data_resources(self) -> None:
        out = handle_resources_list(None, _ctx(_server()))
        assert [r["uri"] for r in out["resources"]] == [URI]

    def test_the_listing_entry_carries_the_ui_metadata(self) -> None:
        """A host may prefetch the view straight off the listing, before any
        tool call — so the CSP has to be there too, not only on the read."""
        server = _server(ui=UIResourceMeta(prefers_border=True))
        out = handle_resources_list(None, _ctx(server))

        assert out["resources"][0]["_meta"] == {UI_META_KEY: {"prefersBorder": True}}

    def test_a_concrete_ui_uri_is_not_a_template(self) -> None:
        """Brace-free, so it lands in ``resources/list`` rather than
        ``resources/templates/list``. Views are concrete by design."""
        server = _server()
        assert server.resources.templates() == []

    def test_guarding_a_view_hides_it_from_the_listing(self, settings: Any) -> None:
        settings.REST_FRAMEWORK_MCP = {
            **settings.REST_FRAMEWORK_MCP,
            "FILTER_LISTINGS_BY_PERMISSIONS": True,
        }

        class _Deny:
            def has_permission(self, request: Any, token: Any) -> bool:
                return False

        server = _server(permissions=[_Deny()])
        out = handle_resources_list(None, _ctx(server))

        assert out["resources"] == []


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_template_backed_view_renders(reader: str) -> None:
    server = _server(html=None, template_name="mcp_ui/view.html")
    ctx = _ctx(server)
    out = (
        handle_resources_read({"uri": URI}, ctx)
        if reader == "sync"
        else await handle_resources_read_async({"uri": URI}, ctx)
    )

    assert "<h1>A view</h1>" in _contents(out)["text"]


@pytest.mark.parametrize("reader", ["sync", "async"])
async def test_a_body_template_backed_view_serves_a_whole_document(reader: str) -> None:
    """The package-composed shell has to survive the read path intact — TEXT
    encoding and all — or the host receives a quoted string rather than a
    document, and renders nothing."""
    server = _server(html=None, body_template_name="mcp_ui/body.html")
    ctx = _ctx(server)
    out = (
        handle_resources_read({"uri": URI}, ctx)
        if reader == "sync"
        else await handle_resources_read_async({"uri": URI}, ctx)
    )
    text = _contents(out)["text"]

    assert text.startswith("<!doctype html>")
    assert '<div id="rows">Waiting for results.</div>' in text
    assert "ui/notifications/initialized" in text
