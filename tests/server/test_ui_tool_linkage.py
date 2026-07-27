"""Linking a tool to an interactive view — ``register_*_tool(ui=...)``."""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework.permissions import IsAuthenticated
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.constants import UI_META_KEY, UIVisibility
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.ui_tool_meta import UIToolMeta
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

VIEW_URI = "ui://invoices/table.html"


def _server(**config: Any) -> MCPServer:
    """A server with the view already registered — links resolve against it."""
    server = MCPServer(
        name="test",
        description="d",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(**config) if config else None,
    )
    server.register_ui_resource(name="table", uri=VIEW_URI, html="<h1>Invoices</h1>")
    return server


def _selector_spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda: None,
        permission_classes=[IsAuthenticated],
    )


def _service_spec() -> ServiceSpec:
    def svc(*, data: dict) -> dict:
        return data

    return ServiceSpec(service=svc, permission_classes=[IsAuthenticated])


def _link(server: MCPServer, **overrides: Any) -> Any:
    return server.register_selector_tool(
        **{
            "name": "list_invoices",
            "spec": _selector_spec(),
            "ui": UIToolMeta(resource_uri=VIEW_URI),
            **overrides,
        }
    )


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        config=server._config,
    )


class TestTheLink:
    def test_lands_under_the_extension_key(self) -> None:
        assert _link(_server()).meta == {UI_META_KEY: {"resourceUri": VIEW_URI}}

    def test_reaches_tools_list(self) -> None:
        server = _server()
        _link(server)
        out = handle_tools_list(None, _ctx(server))

        assert out["tools"][0]["_meta"] == {UI_META_KEY: {"resourceUri": VIEW_URI}}

    def test_visibility_serialises_as_wire_values(self) -> None:
        binding = _link(
            _server(),
            ui=UIToolMeta(resource_uri=VIEW_URI, visibility=[UIVisibility.APP]),
        )

        assert binding.meta[UI_META_KEY]["visibility"] == ["app"]

    def test_unsaid_visibility_is_omitted(self) -> None:
        """Empty means "didn't say", which hosts read as the model-callable
        default — not the same as declaring an empty list."""
        assert "visibility" not in _link(_server()).meta[UI_META_KEY]

    def test_an_unlinked_tool_gains_no_meta(self) -> None:
        server = _server()
        binding = server.register_selector_tool(name="plain", spec=_selector_spec())

        assert binding.meta == {}

    def test_other_extensions_keep_their_keys(self) -> None:
        binding = _link(_server(), meta={"example.com/other": {"k": 1}})
        assert set(binding.meta) == {UI_META_KEY, "example.com/other"}


class TestEveryToolKind:
    def test_a_service_tool_can_link(self) -> None:
        server = _server()
        binding = server.register_service_tool(
            name="refund", spec=_service_spec(), ui=UIToolMeta(resource_uri=VIEW_URI)
        )

        assert binding.meta[UI_META_KEY]["resourceUri"] == VIEW_URI

    def test_a_chain_tool_can_link(self) -> None:
        server = _server()
        binding = server.register_chain_tool(
            name="chained",
            steps=[ChainStep(alias="one", spec=_selector_spec())],
            permissions=[object()],
            ui=UIToolMeta(resource_uri=VIEW_URI),
        )

        assert binding.meta[UI_META_KEY]["resourceUri"] == VIEW_URI

    def test_the_decorator_form_can_link(self) -> None:
        server = _server()

        @server.selector_tool(
            name="decorated",
            kind=SelectorKind.LIST,
            permissions=[object()],
            ui=UIToolMeta(resource_uri=VIEW_URI),
        )
        def decorated() -> None: ...

        assert server.tools.get("decorated").meta[UI_META_KEY]["resourceUri"] == VIEW_URI


class TestRefusedLinks:
    def test_a_uri_no_view_answers_to(self) -> None:
        """A typo would otherwise reach the host as a dangling reference: the
        view renders nothing and nothing reports why."""
        with pytest.raises(ValueError, match="not a view registered on this server"):
            _link(_server(), ui=UIToolMeta(resource_uri="ui://typo.html"))

    def test_a_data_resource_is_not_a_view(self) -> None:
        server = _server()
        server.register_resource(
            name="invoice",
            uri_template="invoices://data",
            selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None),
        )

        with pytest.raises(ValueError, match="not a view registered on this server"):
            _link(server, ui=UIToolMeta(resource_uri="invoices://data"))

    def test_a_tool_that_emits_no_structured_content(self) -> None:
        """``structuredContent`` *is* the render payload — the view would come
        up blank."""
        with pytest.raises(ValueError, match="does not emit structuredContent"):
            _link(_server(), include_structured_content=False)

    def test_the_server_wide_default_being_off_is_caught_too(self) -> None:
        """And the message points at the setting, not the registration."""
        server = _server(include_structured_content=False)

        with pytest.raises(ValueError, match=r"INCLUDE_STRUCTURED_CONTENT"):
            _link(server)

    def test_an_explicit_opt_in_beats_the_server_wide_default(self) -> None:
        server = _server(include_structured_content=False)
        binding = _link(server, include_structured_content=True)

        assert binding.meta[UI_META_KEY]["resourceUri"] == VIEW_URI

    def test_declaring_the_ui_key_twice(self) -> None:
        with pytest.raises(ValueError, match="both ui= and a 'ui' key"):
            _link(_server(), meta={UI_META_KEY: {"resourceUri": "ui://other.html"}})

    def test_a_hand_written_ui_key_alone_is_allowed(self) -> None:
        """The escape hatch stays open — and is unvalidated, by definition."""
        server = _server()
        binding = server.register_selector_tool(
            name="raw", spec=_selector_spec(), meta={UI_META_KEY: {"future": True}}
        )

        assert binding.meta == {UI_META_KEY: {"future": True}}

    def test_nothing_is_registered_when_a_link_is_refused(self) -> None:
        server = _server()
        with pytest.raises(ValueError):
            _link(server, ui=UIToolMeta(resource_uri="ui://typo.html"))

        assert len(server.tools) == 0
