"""Registering a tool with no description warns (or refuses).

The counterpart to ``test_unguarded_warning``. Both properties are required for
a tool to be usable by a model — something must gate the call, something must
say what it does — and until now only the first was checked, so an undescribed
tool shipped to ``tools/list`` indistinguishable from a documented one.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.server.utils import UndescribedToolWarning
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _service() -> dict[str, Any]:
    return {}


def _selector() -> list[Any]:
    return []


def test_undescribed_service_tool_warns() -> None:
    server = _server()
    with pytest.warns(UndescribedToolWarning, match="'x' is registered with no description"):
        server.register_service_tool(name="x", spec=ServiceSpec(service=_service, atomic=False))


def test_undescribed_selector_tool_warns() -> None:
    server = _server()
    with pytest.warns(UndescribedToolWarning):
        server.register_selector_tool(
            name="x", spec=SelectorSpec(kind=SelectorKind.LIST, selector=_selector)
        )


def test_undescribed_chain_tool_warns() -> None:
    server = _server()
    with pytest.warns(UndescribedToolWarning):
        server.register_chain_tool(
            name="x", steps=[ChainStep("s", ServiceSpec(service=_service, atomic=False))]
        )


def test_a_description_silences_the_warning(recwarn: pytest.WarningsRecorder) -> None:
    server = _server()
    server.register_service_tool(
        name="x",
        spec=ServiceSpec(service=_service, atomic=False),
        description="Archive a widget so it stops appearing in listings.",
    )
    assert not [w for w in recwarn if issubclass(w.category, UndescribedToolWarning)]


def test_whitespace_is_not_a_description() -> None:
    """Otherwise `description=" "` silences the check while `tools/list` stays blank."""
    server = _server()
    with pytest.warns(UndescribedToolWarning):
        server.register_service_tool(
            name="x", spec=ServiceSpec(service=_service, atomic=False), description="   \n "
        )


def test_a_docstring_on_the_service_does_not_count() -> None:
    """No silent fallback: a docstring is written for the next developer.

    Promoting it to a tool description would ship prose nobody reviewed for a
    model audience, and would silence the warning that prompts writing the
    right thing.
    """

    def documented_service() -> dict[str, Any]:
        """Internal helper. Assumes the caller already checked tenancy."""
        return {}

    server = _server()
    with pytest.warns(UndescribedToolWarning):
        server.register_service_tool(
            name="x", spec=ServiceSpec(service=documented_service, atomic=False)
        )
    assert server.tools.get("x").description is None


def test_require_tool_descriptions_refuses_registration(settings: Any) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "REQUIRE_TOOL_PERMISSIONS": False,
        "REQUIRE_TOOL_DESCRIPTIONS": True,
    }
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="no description"):
        server.register_service_tool(name="x", spec=ServiceSpec(service=_service, atomic=False))
    assert server.tools.get("x") is None


def test_warning_can_be_filtered_by_category() -> None:
    """The dedicated category exists so consumers can target it precisely."""
    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("error", UndescribedToolWarning)
        with pytest.raises(UndescribedToolWarning):
            server.register_service_tool(name="x", spec=ServiceSpec(service=_service, atomic=False))
