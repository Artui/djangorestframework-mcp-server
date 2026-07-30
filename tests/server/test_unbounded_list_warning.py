"""Registering an unpaginated LIST selector warns (or refuses).

The third member of the registration-time family, after ``test_unguarded_warning``
and ``test_undescribed_warning``, and the one with a production incident behind
it: an unpaginated LIST tool serialises whatever its selector resolves to, which
for a plain ``Model.objects.all()`` is the whole table.

It warns rather than clamping because there is nowhere honest to put the truth:
a paginated result carries ``hasNext`` / ``totalPages``, so a clamped page tells
the model there is more, while a silently shortened unpaginated list looks
complete to the model reading it.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.server.utils import UnboundedListWarning
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server(**config: Any) -> MCPServer:
    return MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(**config) if config else None,
    )


def _selector() -> list[Any]:
    return []


def _register(server: MCPServer, **kwargs: Any) -> Any:
    return server.register_selector_tool(
        name="x",
        description="d",
        permissions=[],
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_selector),
        **kwargs,
    )


def test_unpaginated_list_tool_warns() -> None:
    server = _server()
    with warnings.catch_warnings():
        # The same registration is also unguarded; this test is about the
        # pagination warning alone.
        warnings.simplefilter("ignore")
        warnings.simplefilter("always", UnboundedListWarning)
        with pytest.warns(UnboundedListWarning, match="registered with paginate=False"):
            _register(server)


def test_the_warning_names_both_remedies() -> None:
    server = _server()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _register(server)
    message = str(next(w.message for w in caught if w.category is UnboundedListWarning))
    assert "paginate=True" in message
    assert "REQUIRE_LIST_PAGINATION" in message
    assert "MAX_RESULT_BYTES" in message


def test_paginated_list_tool_is_silent() -> None:
    server = _server()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _register(server, paginate=True)
    assert not [w for w in caught if w.category is UnboundedListWarning]


def test_retrieve_selector_is_exempt() -> None:
    """A RETRIEVE selector returns one instance — bounded by construction."""
    server = _server()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        server.register_selector_tool(
            name="one",
            description="d",
            permissions=[],
            spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_selector),
        )
    assert not [w for w in caught if w.category is UnboundedListWarning]


def test_require_list_pagination_refuses_the_registration() -> None:
    server = _server(require_list_pagination=True)
    with pytest.raises(ImproperlyConfigured, match="registered with paginate=False"):
        _register(server)


def test_require_list_pagination_allows_a_paginated_tool() -> None:
    server = _server(require_list_pagination=True)
    binding = _register(server, paginate=True)
    assert binding.paginate is True
