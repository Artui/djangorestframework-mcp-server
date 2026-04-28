"""``ServiceSpec.kwargs`` / ``SelectorSpec.kwargs`` providers wired through MCP.

The sister repo ships ``kwargs`` as a per-spec callable that returns extra
kwargs to merge into the dispatch pool. MCP exposes this through
:class:`MCPServiceView` so providers see the right shape (request, action
name, URI-template variables on resources).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework.request import Request as DRFRequest
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_binding import ToolBinding
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.server.mcp_service_view import MCPServiceView


def _ctx(*, tools=None, resources=None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user="alice"),
        tools=tools or ToolRegistry(),
        resources=resources or ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


# ---------- ServiceSpec.kwargs (tools/call) ----------


def test_service_spec_kwargs_provider_merged_into_pool() -> None:
    """The provider's return dict reaches the service callable as kwargs."""
    captured: dict[str, Any] = {}

    def svc(*, data: dict, tenant_id: int) -> dict:
        captured["tenant_id"] = tenant_id
        return {"tenant_id": tenant_id}

    def kwargs_provider(view, request) -> dict[str, Any]:
        # The provider sees both the synthesised view and the request — record
        # what arrived so the test can assert on the wire shape.
        captured["view_action"] = view.action
        captured["view_kwargs"] = view.kwargs
        return {"tenant_id": 42}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t.x",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False, kwargs=kwargs_provider),
        )
    )
    out = handle_tools_call({"name": "t.x", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"tenant_id": 42}
    assert captured["tenant_id"] == 42
    assert captured["view_action"] == "t.x"
    assert captured["view_kwargs"] == {}


def test_service_spec_kwargs_provider_receives_drf_request() -> None:
    seen: dict[str, Any] = {}

    def svc(*, data: dict, label: str) -> dict:
        return {"label": label}

    def kwargs_provider(view, request) -> dict[str, Any]:
        seen["request_user"] = getattr(request, "user", None)
        seen["request_is_drf"] = isinstance(request, DRFRequest)
        return {"label": "ok"}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False, kwargs=kwargs_provider),
        )
    )
    handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert seen["request_user"] == "alice"
    assert seen["request_is_drf"] is True


async def test_async_service_spec_kwargs_provider_merged_into_pool() -> None:
    def svc(*, data: dict, tenant_id: int) -> dict:
        return {"tenant_id": tenant_id}

    def kwargs_provider(view, request) -> dict[str, Any]:
        return {"tenant_id": 7}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False, kwargs=kwargs_provider),
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"tenant_id": 7}


# ---------- SelectorSpec acceptance + kwargs (resources/read) ----------


def test_register_resource_accepts_selector_spec() -> None:
    """A ``SelectorSpec`` flows through ``register_resource`` end-to-end."""
    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
    from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )

    def get_invoice(*, pk: str) -> dict:
        return {"pk": pk, "via": "spec"}

    spec = SelectorSpec(selector=get_invoice)
    binding = server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=spec,  # type: ignore[arg-type]
    )
    assert binding.selector is get_invoice
    assert binding.kwargs_provider is None  # spec had no kwargs


def test_selector_spec_with_none_selector_is_rejected() -> None:
    """A spec with no concrete callable can't be dispatched — fail loudly."""
    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
    from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    with pytest.raises(ValueError, match="selector=None"):
        server.register_resource(
            name="empty",
            uri_template="x://",
            selector=SelectorSpec(selector=None),  # type: ignore[arg-type]
        )


def test_selector_spec_output_serializer_used_when_caller_omits() -> None:
    """The spec's ``output_serializer`` fills in when the caller doesn't pass one."""
    from rest_framework import serializers

    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
    from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

    class OutSer(serializers.Serializer):
        pk = serializers.CharField()

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    spec = SelectorSpec(selector=lambda *, pk: {"pk": pk}, output_serializer=OutSer)
    binding = server.register_resource(
        name="r",
        uri_template="r://{pk}",
        selector=spec,  # type: ignore[arg-type]
    )
    assert binding.output_serializer is OutSer


def test_selector_spec_caller_output_serializer_wins() -> None:
    """Explicit caller arg trumps the spec's value (intentional override)."""
    from rest_framework import serializers

    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
    from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

    class FromSpec(serializers.Serializer):
        pass

    class FromCaller(serializers.Serializer):
        pass

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    spec = SelectorSpec(selector=lambda: {}, output_serializer=FromSpec)
    binding = server.register_resource(
        name="r",
        uri_template="r://",
        selector=spec,  # type: ignore[arg-type]
        output_serializer=FromCaller,
    )
    assert binding.output_serializer is FromCaller


def test_selector_spec_kwargs_provider_invoked_on_read() -> None:
    """``SelectorSpec.kwargs`` runs on every ``resources/read`` and merges into the pool."""
    from rest_framework_mcp.registry.resource_binding import ResourceBinding

    seen: dict[str, Any] = {}

    def selector(*, pk: str, tenant_id: int) -> dict:
        seen["selector_pk"] = pk
        seen["selector_tenant"] = tenant_id
        return {"pk": pk, "tenant": tenant_id}

    def provider(view, request) -> dict[str, Any]:
        # URI-template variables are exposed through ``view.kwargs`` so
        # providers can branch on them without parsing the URI again.
        seen["view_kwargs"] = view.kwargs
        return {"tenant_id": 99}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{pk}",
            description=None,
            selector=selector,
            kwargs_provider=provider,
        )
    )
    out = handle_resources_read({"uri": "r://7"}, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert seen["selector_pk"] == "7"
    assert seen["selector_tenant"] == 99
    assert seen["view_kwargs"] == {"pk": "7"}


async def test_async_selector_spec_kwargs_provider_invoked_on_read() -> None:
    from rest_framework_mcp.registry.resource_binding import ResourceBinding

    def selector(*, pk: str, tenant_id: int) -> dict:
        return {"pk": pk, "tenant": tenant_id}

    def provider(view, request) -> dict[str, Any]:
        return {"tenant_id": 11}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{pk}",
            description=None,
            selector=selector,
            kwargs_provider=provider,
        )
    )
    out = await handle_resources_read_async({"uri": "r://5"}, _ctx(resources=resources))
    assert isinstance(out, dict)
    text = out["contents"][0]["text"]
    import json

    assert json.loads(text) == {"pk": "5", "tenant": 11}


# ---------- MCPServiceView shape ----------


def test_mcp_service_view_default_kwargs_is_empty_dict() -> None:
    """``kwargs`` defaults to ``{}`` — the most common shape on tool calls."""
    request = HttpRequest()
    view = MCPServiceView(request=request, action="x")
    assert view.kwargs == {}


def test_mcp_service_view_is_frozen() -> None:
    """The adapter is immutable — providers can't accidentally mutate dispatch state."""
    import dataclasses

    view = MCPServiceView(request=HttpRequest(), action="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.action = "y"  # type: ignore[misc]
