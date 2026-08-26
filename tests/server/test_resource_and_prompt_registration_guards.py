"""Registration-time guards on resources and prompts.

The unguarded-binding check started life on tools, and a selector registered as
a *resource* used to start clean while the identical spec registered as a tool
raised. The URI-template channel had the matching gap on names: it is the one
caller-controlled name channel in the package, and unlike ``url_kwargs`` /
``query_params`` it went through no validation at all, so ``notes://{user}``
compiled to a regex group that overwrote the authenticated principal.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import IsAuthenticated
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import PromptArgument
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.permissions.scope_required import ScopeRequired
from rest_framework_mcp.registry.types.ui_resource_meta import UIResourceMeta
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.server.utils import UnguardedToolWarning
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _selector() -> list[Any]:
    return []


# ---------- unguarded resources and prompts ----------


def test_unguarded_resource_warns() -> None:
    server = _server()
    with pytest.warns(UnguardedToolWarning, match="MCP resource 'r' is registered with no"):
        server.register_resource(
            name="r",
            uri_template="r://all",
            selector=SelectorSpec(kind=SelectorKind.LIST, selector=_selector),
        )


def test_unguarded_prompt_warns() -> None:
    server = _server()
    with pytest.warns(UnguardedToolWarning, match="MCP prompt 'p' is registered with no"):
        server.register_prompt(name="p", render=lambda **_: "x")


def test_resource_spec_permission_classes_silence_the_warning(
    recwarn: pytest.WarningsRecorder,
) -> None:
    server = _server()
    server.register_resource(
        name="r",
        uri_template="r://all",
        selector=SelectorSpec(
            kind=SelectorKind.LIST, selector=_selector, permission_classes=[IsAuthenticated]
        ),
    )
    assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]


def test_prompt_binding_permissions_silence_the_warning(recwarn: pytest.WarningsRecorder) -> None:
    server = _server()
    server.register_prompt(
        name="p", render=lambda **_: "x", permissions=[ScopeRequired("prompts:read")]
    )
    assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]


def test_require_tool_permissions_refuses_an_unguarded_resource(settings: Any) -> None:
    """The asymmetry the guard closes: the same spec, registered as a tool,
    already raised here."""
    settings.REST_FRAMEWORK_MCP = {"REQUIRE_TOOL_PERMISSIONS": True}
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="MCP resource 'r'"):
        server.register_resource(
            name="r",
            uri_template="r://all",
            selector=SelectorSpec(kind=SelectorKind.LIST, selector=_selector),
        )
    assert server.resources.by_uri_template("r://all") is None


def test_require_tool_permissions_refuses_an_unguarded_prompt(settings: Any) -> None:
    settings.REST_FRAMEWORK_MCP = {"REQUIRE_TOOL_PERMISSIONS": True}
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="MCP prompt 'p'"):
        server.register_prompt(name="p", render=lambda **_: "x")
    assert server.prompts.get("p") is None


def test_an_unguarded_view_is_deliberately_allowed(
    settings: Any, recwarn: pytest.WarningsRecorder
) -> None:
    """A view's content sources cannot read the caller's data — a template with
    no context, a literal document, or a zero-argument callable — so views stay
    exempt on purpose rather than by oversight."""
    settings.REST_FRAMEWORK_MCP = {"REQUIRE_TOOL_PERMISSIONS": True}
    server = _server()
    server.register_ui_resource(
        name="view",
        uri="ui://view",
        html="<p>hi</p>",
        ui=UIResourceMeta(),
    )
    assert server.resources.by_uri_template("ui://view") is not None
    assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]


# ---------- URI-template variable names ----------


@pytest.mark.parametrize("reserved", ["user", "request", "data", "instance"])
def test_a_template_variable_named_after_a_pool_seed_is_refused(reserved: str) -> None:
    """A template variable lands in the selector's kwarg pool, so one named
    after a dispatcher seed would let a URI segment stand in for it."""
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="collide with reserved transport keys"):
        server.register_resource(
            name="notes",
            uri_template=f"notes://{{{reserved}}}/{{pk}}",
            selector=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda **_: {},
                permission_classes=[IsAuthenticated],
            ),
        )
    assert server.resources.resolve("notes://victim/1") is None


def test_a_duplicated_template_variable_is_refused() -> None:
    """The later capture silently shadows the earlier one, so it is caught at
    registration by the same shared check."""
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="duplicate uri_template variable"):
        server.register_resource(
            name="notes",
            uri_template="notes://{pk}/{pk}",
            selector=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda **_: {},
                permission_classes=[IsAuthenticated],
            ),
        )


def test_pagination_names_are_not_reserved_for_a_resource_template() -> None:
    """A resource has no post-fetch pipeline, so ``page`` is an ordinary
    locator segment here even though a tool reserves the name."""
    server = _server()
    binding = server.register_resource(
        name="docs",
        uri_template="docs://{page}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda page: {"page": page},
            permission_classes=[IsAuthenticated],
        ),
    )
    assert binding.uri_template == "docs://{page}"


def test_an_ordinary_template_variable_still_registers() -> None:
    server = _server()
    binding = server.register_resource(
        name="notes",
        uri_template="notes://{pk}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda pk: {"pk": pk},
            permission_classes=[IsAuthenticated],
        ),
        completions={"pk": lambda value: ["1"]},
    )
    assert binding.is_template
    assert "pk" in binding.completions


def test_a_prompt_argument_list_is_unaffected() -> None:
    """Prompt arguments are a different channel: the ``prompts/get`` seed order
    is what protects them, so a declared name is never refused here."""
    server = _server()
    binding = server.register_prompt(
        name="p",
        render=lambda **_: "x",
        arguments=[PromptArgument(name="topic")],
        permissions=[ScopeRequired("prompts:read")],
    )
    assert binding.arguments[0].name == "topic"
