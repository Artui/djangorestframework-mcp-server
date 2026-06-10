"""PERM-1: registering a tool with no permissions warns (or refuses)."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import IsAuthenticated
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.permissions.scope_required import ScopeRequired
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.server.utils import UnguardedToolWarning
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _service() -> dict[str, Any]:
    return {}


def _selector() -> list[Any]:
    return []


def test_unguarded_service_tool_warns() -> None:
    server = _server()
    with pytest.warns(UnguardedToolWarning, match="'x' is registered with no permissions"):
        server.register_service_tool(name="x", spec=ServiceSpec(service=_service, atomic=False))


def test_unguarded_selector_tool_warns() -> None:
    server = _server()
    with pytest.warns(UnguardedToolWarning):
        server.register_selector_tool(
            name="x", spec=SelectorSpec(kind=SelectorKind.LIST, selector=_selector)
        )


def test_unguarded_chain_tool_warns() -> None:
    server = _server()
    with pytest.warns(UnguardedToolWarning):
        server.register_chain_tool(
            name="x", steps=[ChainStep("s", ServiceSpec(service=_service, atomic=False))]
        )


def test_binding_permissions_silence_the_warning(recwarn: pytest.WarningsRecorder) -> None:
    server = _server()
    server.register_service_tool(
        name="x",
        spec=ServiceSpec(service=_service, atomic=False),
        permissions=[ScopeRequired("tools:write")],
    )
    assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]


def test_spec_permission_classes_silence_the_warning(recwarn: pytest.WarningsRecorder) -> None:
    server = _server()
    server.register_service_tool(
        name="x",
        spec=ServiceSpec(service=_service, atomic=False, permission_classes=[IsAuthenticated]),
    )
    assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]


def test_require_tool_permissions_refuses_registration(settings: Any) -> None:
    settings.REST_FRAMEWORK_MCP = {"REQUIRE_TOOL_PERMISSIONS": True}
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="no permissions"):
        server.register_service_tool(name="x", spec=ServiceSpec(service=_service, atomic=False))
    assert server.tools.get("x") is None


def test_warning_can_be_filtered_by_category() -> None:
    """The dedicated category exists so consumers can target it precisely."""
    import warnings

    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnguardedToolWarning)
        with pytest.raises(UnguardedToolWarning):
            server.register_service_tool(name="x", spec=ServiceSpec(service=_service, atomic=False))
