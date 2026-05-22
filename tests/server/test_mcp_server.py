from __future__ import annotations

import pytest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _make() -> MCPServer:
    """Build a server that doesn't depend on Django settings for collaborators."""
    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend

    return MCPServer(
        name="test",
        description="d",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )


def test_register_tool_imperative() -> None:
    server = _make()

    def svc(*, data: dict) -> dict:
        return data

    binding = server.register_service_tool(name="t", spec=ServiceSpec(service=svc))
    assert server.tools.get("t") is binding


def test_register_resource_imperative() -> None:
    server = _make()
    binding = server.register_resource(
        name="r",
        uri_template="r://",
        selector=SelectorSpec(kind=SelectorKind.LIST, selector=lambda: None),
    )
    assert server.resources.resolve("r://") is not None
    assert binding.name == "r"


def test_register_resource_rejects_bare_callable() -> None:
    """The imperative surface requires a ``SelectorSpec``."""
    server = _make()
    with pytest.raises(TypeError, match="SelectorSpec"):
        server.register_resource(
            name="r",
            uri_template="r://",
            selector=lambda: None,  # type: ignore[arg-type],
        )


def test_tool_decorator_uses_function_doc_as_description() -> None:
    server = _make()

    @server.service_tool(name="t.create")
    def create(*, data: dict) -> dict:
        """Create something."""
        return data

    assert server.tools.get("t.create").description == "Create something."


def test_tool_decorator_with_explicit_spec() -> None:
    server = _make()

    def svc(*, data: dict) -> dict:
        return data

    @server.service_tool(name="t.x", spec=ServiceSpec(service=svc))
    def placeholder(*, data: dict) -> dict:
        return {"ignored": True}

    # Decorator returns the original function unchanged.
    assert placeholder(data={"a": 1}) == {"ignored": True}
    binding = server.tools.get("t.x")
    assert binding.spec.service is svc


def test_resource_decorator_uses_function_name_when_unspecified() -> None:
    server = _make()

    @server.resource(uri_template="x://{pk}", kind=SelectorKind.RETRIEVE)
    def get_x(*, pk: int) -> int:
        """Fetch an x."""
        return pk

    found = server.resources.resolve("x://7")
    assert found is not None
    binding, _ = found
    assert binding.name == "get_x"
    assert binding.description == "Fetch an x."


def test_resource_decorator_overrides_name() -> None:
    server = _make()

    @server.resource(uri_template="y://", name="custom", kind=SelectorKind.LIST)
    def listy() -> list:
        return []

    found = server.resources.resolve("y://")
    assert found is not None and found[0].name == "custom"


def test_service_tool_decorator_builds_output_selector_spec_when_serializer_given() -> None:
    """The flat ``output_serializer`` decorator kwarg flows into a
    nested ``output_selector_spec`` (sister-repo 0.13+).
    """
    from rest_framework import serializers

    class _Out(serializers.Serializer):
        x = serializers.IntegerField()

    server = _make()

    @server.service_tool(name="t.create", output_serializer=_Out)
    def create(*, data: dict) -> dict:
        return data

    binding = server.tools.get("t.create")
    assert binding.spec.output_selector_spec is not None
    assert binding.spec.output_selector_spec.output_serializer is _Out
    assert binding.spec.output_selector_spec.kind is SelectorKind.RETRIEVE


def test_selector_tool_decorator_requires_kind_when_spec_omitted() -> None:
    server = _make()
    with pytest.raises(TypeError, match="``kind`` is required"):

        @server.selector_tool(name="t")
        def fn() -> list:
            return []


def test_selector_tool_decorator_accepts_explicit_spec() -> None:
    """When ``spec=`` is passed, ``kind`` kwarg is ignored and spec.kind wins."""
    server = _make()

    @server.selector_tool(name="t", spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda: []))
    def fn() -> list:
        return []

    binding = server.tools.get("t")
    assert binding.kind is SelectorKind.LIST


def test_resource_decorator_requires_kind_when_spec_omitted() -> None:
    server = _make()
    with pytest.raises(TypeError, match="``kind`` is required"):

        @server.resource(uri_template="x://")
        def fn() -> list:
            return []


def test_resource_decorator_accepts_explicit_spec() -> None:
    server = _make()

    @server.resource(
        uri_template="x://",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda: []),
    )
    def fn() -> list:
        return []

    found = server.resources.resolve("x://")
    assert found is not None
    binding, _ = found
    assert binding.kind is SelectorKind.LIST


def test_accessors() -> None:
    server = _make()
    assert server.auth_backend is not None
    assert server.session_store is not None


def test_default_loads_use_settings(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
    }
    server = MCPServer(name="s")
    assert server.auth_backend is not None
    assert server.session_store is not None


def test_register_tool_duplicate_raises() -> None:
    server = _make()
    server.register_service_tool(name="dup", spec=ServiceSpec(service=lambda: None))
    with pytest.raises(ValueError, match="Duplicate"):
        server.register_service_tool(name="dup", spec=ServiceSpec(service=lambda: None))
