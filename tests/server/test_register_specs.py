"""``MCPServer.register_specs`` — bulk registration from a shared SpecRegistry."""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import RegisteredSpec, SelectorKind, SpecRegistry, Unrestricted
from django_service_specs import SelectorSpec as KernelSelectorSpec
from django_service_specs import ServiceSpec as KernelServiceSpec
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_services import PolymorphicServiceSpec, QueryParam
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.server.utils import UnguardedToolWarning
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _make() -> MCPServer:
    return MCPServer(
        name="test",
        description="d",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )


class _Payload(serializers.Serializer):
    text = serializers.CharField()


# ``guarded=False`` is ``permission_classes=[]``, not ``None``: since drf-services
# 0.58.0 ``SpecRegistry.register`` refuses a spec declaring no permissions at all,
# so ``[]`` (registered "with no guards", as its CHANGELOG puts it) is the only
# unguarded spec a registry can hand this mount.
def _service_spec(*, guarded: bool = True) -> ServiceSpec:
    def svc(*, data: dict) -> dict:
        return data

    # The serializer is what fills ``data``: without one, registration refuses
    # a service requiring it, since nothing would.
    return ServiceSpec(
        service=svc,
        input_serializer=_Payload,
        permission_classes=[IsAuthenticated] if guarded else [],
    )


def _selector_spec(*, guarded: bool = True) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda: None,
        permission_classes=[IsAuthenticated] if guarded else [],
    )


def _populated() -> SpecRegistry:
    registry = SpecRegistry()
    registry.register("list_orders", _selector_spec(), tags=("read", "public"))
    registry.register("refund_order", _service_spec(), tags=("write", "admin"))
    return registry


def _read_only() -> SpecRegistry:
    registry = SpecRegistry()
    registry.register("list_orders", _selector_spec(), tags=("read",))
    return registry


class TestBulkRegistration:
    def test_registers_every_entry(self) -> None:
        server = _make()
        server.register_specs(_populated())

        assert len(server.tools) == 2
        assert "list_orders" in server.tools
        assert "refund_order" in server.tools

    def test_discriminates_service_from_selector_by_spec_type(self) -> None:
        server = _make()
        server.register_specs(_populated())

        assert isinstance(server.tools.get("refund_order"), ToolBinding)
        assert isinstance(server.tools.get("list_orders"), SelectorToolBinding)

    def test_returns_bindings_in_registration_order(self) -> None:
        server = _make()
        bindings = server.register_specs(_populated())

        assert [b.name for b in bindings] == ["list_orders", "refund_order"]
        assert server.tools.get("list_orders") is bindings[0]

    def test_an_empty_registry_registers_nothing(self) -> None:
        server = _make()
        assert server.register_specs(SpecRegistry()) == ()
        assert len(server.tools) == 0

    def test_a_filtered_view_registers_only_its_entries(self) -> None:
        """The multi-mount case: two servers fed different projections."""
        registry = _populated()
        public, admin = _make(), _make()

        public.register_specs(registry.by_tag("public"))
        admin.register_specs(registry.by_tag("admin"))

        assert [b.name for b in public.tools.all()] == ["list_orders"]
        assert [b.name for b in admin.tools.all()] == ["refund_order"]

    def test_collides_with_an_already_registered_tool_name(self) -> None:
        """Names share the one MCP tool namespace — the registry is a source
        for it, not a second namespace."""
        server = _make()
        server.register_service_tool(name="refund_order", spec=_service_spec())

        with pytest.raises(ValueError, match="Duplicate MCP tool name"):
            server.register_specs(_populated())


class TestOverrides:
    def test_forwards_knobs_to_the_registration_method(self) -> None:
        server = _make()
        (binding,) = server.register_specs(
            _read_only(),
            overrides={"list_orders": {"paginate": True, "max_page_size": 25}},
        )

        assert isinstance(binding, SelectorToolBinding)
        assert binding.paginate is True
        assert binding.max_page_size == 25

    def test_meta_rides_the_overrides_map(self) -> None:
        """``_meta`` is per-transport, so it is a binding knob rather than
        something the shared registry could carry."""
        server = _make()
        (binding,) = server.register_specs(
            _read_only(),
            overrides={"list_orders": {"meta": {"example.com/panel": {"href": "p://x"}}}},
        )

        assert binding.meta == {"example.com/panel": {"href": "p://x"}}

    def test_entries_without_an_override_use_defaults(self) -> None:
        server = _make()
        bindings = server.register_specs(
            _populated(),
            overrides={"refund_order": {"title": "Refund"}},
        )

        by_name = {b.name: b for b in bindings}
        assert by_name["refund_order"].title == "Refund"
        assert by_name["list_orders"].title is None

    def test_no_overrides_is_the_same_as_an_empty_mapping(self) -> None:
        plain, empty = _make(), _make()
        plain_bindings = plain.register_specs(_populated())
        empty_bindings = empty.register_specs(_populated(), overrides={})

        assert [b.name for b in plain_bindings] == [b.name for b in empty_bindings]

    def test_an_unknown_override_name_raises(self) -> None:
        """A typo would otherwise be a silent no-op."""
        server = _make()
        with pytest.raises(ValueError, match="overrides name specs not in this SpecRegistry"):
            server.register_specs(_populated(), overrides={"lst_orders": {"paginate": True}})

    def test_the_unknown_override_error_lists_the_registered_names(self) -> None:
        server = _make()
        with pytest.raises(ValueError, match=r"list_orders.*refund_order"):
            server.register_specs(_populated(), overrides={"typo": {}})

    def test_nothing_is_registered_when_overrides_are_rejected(self) -> None:
        """The name check runs before any registration."""
        server = _make()
        with pytest.raises(ValueError):
            server.register_specs(_populated(), overrides={"typo": {}})
        assert len(server.tools) == 0

    def test_a_knob_for_the_wrong_spec_kind_raises(self) -> None:
        """``paginate`` is a selector-pipeline knob; a ServiceSpec has no such
        parameter, so the underlying method rejects it."""
        registry = SpecRegistry()
        registry.register("refund_order", _service_spec())

        server = _make()
        with pytest.raises(TypeError, match="paginate"):
            server.register_specs(registry, overrides={"refund_order": {"paginate": True}})

    def test_overrides_are_not_mutated(self) -> None:
        server = _make()
        overrides = {"refund_order": {"title": "Refund"}}
        server.register_specs(_populated(), overrides=overrides)

        assert overrides == {"refund_order": {"title": "Refund"}}


class TestSecurityGuardsStillApply:
    """Bulk registration goes through the per-tool methods, so the
    permission-declaration guard is not bypassed by registering in bulk."""

    def test_an_unguarded_spec_still_warns(self) -> None:
        registry = SpecRegistry()
        registry.register("refund_order", _service_spec(guarded=False))

        server = _make()
        with pytest.warns(UnguardedToolWarning, match="registered with no permissions"):
            server.register_specs(registry)

    def test_require_tool_permissions_still_refuses(self, settings: Any) -> None:
        settings.REST_FRAMEWORK_MCP = {"REQUIRE_TOOL_PERMISSIONS": True}
        registry = SpecRegistry()
        registry.register("refund_order", _service_spec(guarded=False))

        server = _make()
        with pytest.raises(ImproperlyConfigured, match="no permissions"):
            server.register_specs(registry)
        assert len(server.tools) == 0

    def test_a_guarded_spec_does_not_warn(self, recwarn: Any) -> None:
        server = _make()
        server.register_specs(_populated())

        assert not [w for w in recwarn if issubclass(w.category, UnguardedToolWarning)]

    def test_per_binding_permissions_can_be_supplied_by_override(self) -> None:
        """A spec that declares none can still be guarded at the binding."""
        registry = SpecRegistry()
        registry.register("refund_order", _service_spec(guarded=False))

        server = _make()
        (binding,) = server.register_specs(
            registry, overrides={"refund_order": {"permissions": [IsAuthenticated]}}
        )
        assert binding.permissions == (IsAuthenticated,)


# ---------- a spec this server cannot dispatch ----------


def _kernel_service(**_: Any) -> None:
    return None


def _kernel_rows(**_: Any) -> list[Any]:
    return []


class _Declaration:
    """Another library's declaration, compiling to a kernel spec: a ``SpecSource``."""

    def as_kernel_spec(self) -> KernelServiceSpec:
        return KernelServiceSpec(service=_kernel_service, permissions=[Unrestricted()])


@pytest.mark.parametrize(
    ("spec", "type_name"),
    [
        (
            KernelServiceSpec(service=_kernel_service, permissions=[Unrestricted()]),
            "django_service_specs.specs.service_spec.ServiceSpec",
        ),
        (
            KernelSelectorSpec(
                kind=SelectorKind.LIST, selector=_kernel_rows, permissions=[Unrestricted()]
            ),
            "django_service_specs.specs.selector_spec.SelectorSpec",
        ),
        (_Declaration(), f"{__name__}._Declaration"),
    ],
    ids=["kernel-service", "kernel-selector", "spec-source"],
)
def test_a_spec_that_is_not_drf_services_is_refused_naming_the_tool(
    spec: Any, type_name: str
) -> None:
    # The registry holds anything the kernel can compile, and this server
    # dispatches through drf-services' ``dispatch_spec``, which runs a
    # DRF-bound spec. Without the check such an entry reaches the selector
    # registrar and fails on a missing attribute
    # (``'ServiceSpec' object has no attribute 'selector'``).
    registry = SpecRegistry()
    registry.register("place_order", spec)
    server = _make()

    with pytest.raises(TypeError) as caught:
        server.register_specs(registry)

    message = str(caught.value)
    assert "Tool 'place_order'" in message
    assert type_name in message
    assert "is not a rest_framework_services.ServiceSpec or SelectorSpec" in message
    assert "from rest_framework_services import ServiceSpec, SelectorSpec" in message
    assert server.tools.get("place_order") is None


class _HandBuiltRegistry:
    """A registry assembled without ``SpecRegistry.register``, which refuses a
    ``PolymorphicServiceSpec`` itself: the only way one reaches ``register_specs``."""

    def __init__(self, *entries: RegisteredSpec) -> None:
        self._entries = entries

    def __contains__(self, name: object) -> bool:
        return any(entry.name == name for entry in self._entries)

    def all(self) -> tuple[RegisteredSpec, ...]:
        return self._entries


def _refusal(registry: Any) -> str:
    with pytest.raises(TypeError) as caught:
        _make().register_specs(registry)
    return str(caught.value)


def test_the_remedy_follows_the_type_found() -> None:
    # Only the kernel's own spec is pointed at the kernel's dispatch.
    kernel = SpecRegistry()
    kernel.register(
        "place_order", KernelServiceSpec(service=_kernel_service, permissions=[Unrestricted()])
    )
    assert "django_service_specs.dispatch" in _refusal(kernel)

    # Another library's declaration is not a kernel spec, so it is told only
    # which two types this server accepts.
    source = SpecRegistry()
    source.register("place_order", _Declaration())
    source_message = _refusal(source)
    assert "django_service_specs.dispatch" not in source_message
    assert "from rest_framework_services import ServiceSpec, SelectorSpec" in source_message

    # A polymorphic spec is pointed at its variants.
    poly = PolymorphicServiceSpec(
        discriminator=lambda **_: "email",
        specs={"email": ServiceSpec(service=_kernel_service, atomic=False)},
    )
    poly_message = _refusal(
        _HandBuiltRegistry(RegisteredSpec(name="sign_up", spec=poly, tags=frozenset()))
    )
    assert "Tool 'sign_up'" in poly_message
    assert "rest_framework_services.types.polymorphic_service_spec.PolymorphicServiceSpec" in (
        poly_message
    )
    assert "register each variant in its `.specs` mapping" in poly_message
    assert "django_service_specs.dispatch" not in poly_message


# ---------- a QueryParam read naming a selector parameter ----------


def _orders_by_status(*, status: str = "any") -> list[dict[str, Any]]:
    return [{"status": status}]


def _status_read_spec() -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_orders_by_status,
        permission_classes=[AllowAny],
        reads=[QueryParam("status")],
    )


def test_a_query_param_read_naming_a_selector_parameter_is_served_by_both_routes() -> None:
    # The registry recipe says the two routes agree here: the spec's own read
    # says where the parameter comes from, so it is no shadow on either route.
    # It is advertised once and the value reaches the parameter.
    registry = SpecRegistry()
    registry.register("orders", _status_read_spec())
    from_registry = _make()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from_registry.register_specs(registry)

    direct = _make()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        direct.register_selector_tool(name="orders", spec=_status_read_spec())

    for server in (from_registry, direct):
        listed: Any = server.list_tools(user=None)
        assert list(listed["tools"][0]["inputSchema"]["properties"]) == ["status"]
        result = server.call_tool("orders", {"status": "open"}, user=None).to_dict()
        assert result["structuredContent"] == [{"status": "open"}]
