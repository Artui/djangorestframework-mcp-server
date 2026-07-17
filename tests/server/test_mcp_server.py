from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.transport.django_cache_session_store import DjangoCacheSessionStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _make() -> MCPServer:
    """Build a server that doesn't depend on Django settings for collaborators."""
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


def test_omitted_collaborators_get_constructed_defaults() -> None:
    """No dotted-path resolution: the package defaults are built directly."""
    server = MCPServer(name="s")
    assert isinstance(server.auth_backend, DjangoOAuthToolkitBackend)
    assert isinstance(server.session_store, DjangoCacheSessionStore)


def test_default_session_store_is_namespaced_to_the_server() -> None:
    """The cache-backed store shares one Django cache across every server in
    the process, so its key space has to be per-server or two mounts collide."""
    internal = MCPServer(name="i", url_namespace="internal-mcp")
    public = MCPServer(name="p", url_namespace="public-mcp")

    internal_id = internal.session_store.create(principal_id="user:1")

    assert internal.session_store.owner(internal_id) == "user:1"
    assert public.session_store.owner(internal_id) is None


def test_sessions_are_keyed_on_name_not_url_namespace() -> None:
    """Two servers sharing a URL namespace but named differently stay isolated.

    ``url_namespace`` is a routing detail: a server used only in-process (the
    django-ag-ui bridge) is never mounted, so its namespace is a meaningless
    default and would collide with a mounted server at the default namespace —
    a collision Django's urls.W005 duplicate-namespace check cannot see,
    because an unmounted server isn't in the URL conf.
    """
    in_process = MCPServer(name="bridge")  # never mounted; url_namespace is the default
    mounted = MCPServer(name="public-api")  # also the default url_namespace

    session_id = in_process.session_store.create(principal_id="user:1")

    assert in_process.session_store.owner(session_id) == "user:1"
    assert mounted.session_store.owner(session_id) is None


def test_renaming_a_url_prefix_does_not_drop_sessions() -> None:
    """Identity, not routing, owns the key space — so a cosmetic URL refactor
    is not a silent session purge."""
    before = MCPServer(name="stable", url_namespace="old-prefix")
    after = MCPServer(name="stable", url_namespace="new-prefix")

    session_id = before.session_store.create(principal_id="user:7")

    assert after.session_store.owner(session_id) == "user:7"


def test_free_form_names_produce_usable_cache_keys() -> None:
    """``name`` is consumer-supplied prose, but cache keys must survive
    backends (memcached) that reject spaces and control characters."""
    server = MCPServer(name="My Invoicing Server ✨")
    session_id = server.session_store.create(principal_id="user:3")

    assert server.session_store.owner(session_id) == "user:3"


def test_a_hand_built_session_store_keeps_its_own_namespace() -> None:
    """Passing a store opts out of the server's namespacing — the consumer owns
    it, and two default-constructed stores collide exactly as before."""
    store = DjangoCacheSessionStore()
    server = MCPServer(name="s", url_namespace="ignored", session_store=store)
    session_id = server.session_store.create(principal_id="user:2")

    assert DjangoCacheSessionStore().owner(session_id) == "user:2"


@pytest.mark.parametrize(
    "removed",
    [
        {"AUTH_BACKEND": "some.dotted.Path"},
        {"SESSION_STORE": "some.dotted.Path"},
    ],
)
def test_removed_collaborator_settings_raise(settings, removed: dict[str, str]) -> None:
    """Silently ignoring a stale AUTH_BACKEND would mean a project that thinks
    it configured authentication has not — so it fails loudly instead."""
    settings.REST_FRAMEWORK_MCP = removed
    with pytest.raises(ImproperlyConfigured, match="removed in 0.12.0"):
        MCPServer(name="s")


def test_resource_url_configures_the_default_backend() -> None:
    server = MCPServer(name="internal", resource_url="https://example.com/internal/mcp/")
    md = server.auth_backend.protected_resource_metadata()
    assert md.resource == "https://example.com/internal/mcp/"


def test_resource_url_with_a_custom_backend_raises() -> None:
    """A custom backend owns its own audience binding, so resource_url= has
    nowhere to go — silently dropping it would leave enforcement unconfigured
    in a project that believes it configured it."""
    with pytest.raises(ImproperlyConfigured, match="not both"):
        MCPServer(
            name="s",
            resource_url="https://example.com/mcp/",
            auth_backend=AllowAnyBackend(),
        )


def test_removed_settings_error_names_the_replacement(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"AUTH_BACKEND": "some.dotted.Path"}
    with pytest.raises(ImproperlyConfigured, match=r"auth_backend=YourAuthBackend\(\)"):
        MCPServer(name="s")


def test_register_tool_duplicate_raises() -> None:
    server = _make()
    server.register_service_tool(name="dup", spec=ServiceSpec(service=lambda: None))
    with pytest.raises(ValueError, match="Duplicate"):
        server.register_service_tool(name="dup", spec=ServiceSpec(service=lambda: None))


def test_urls_is_the_namespaced_triple() -> None:
    patterns, app_name, namespace = _make().urls
    # (patterns, app_name, namespace) — the shape path() mounts directly, like
    # admin.site.urls (no include()).
    assert app_name == namespace == "mcp"
    assert [p.name for p in patterns] == ["endpoint", "protected-resource-metadata"]


def test_async_urls_is_the_namespaced_triple() -> None:
    patterns, app_name, namespace = _make().async_urls
    assert app_name == namespace == "mcp"
    assert [p.name for p in patterns] == ["endpoint", "protected-resource-metadata"]


def test_url_namespace_is_overridable() -> None:
    server = MCPServer(
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        url_namespace="tools",
    )
    _, app_name, namespace = server.urls
    assert app_name == namespace == "tools"


def test_urls_mount_via_path_and_reverse_namespaced() -> None:
    # tests.testapp.urls mounts path("mcp/", server.urls) (the conftest
    # ROOT_URLCONF), so the namespaced names reverse without include().
    from django.urls import reverse

    assert reverse("mcp:endpoint") == "/mcp/"
    assert reverse("mcp:protected-resource-metadata") == "/mcp/.well-known/oauth-protected-resource"
