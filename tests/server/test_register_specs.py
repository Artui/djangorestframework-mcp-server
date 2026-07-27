"""``MCPServer.register_specs`` — bulk registration from a shared SpecRegistry."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import IsAuthenticated
from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.types.selector_kind import SelectorKind
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


def _service_spec(*, guarded: bool = True) -> ServiceSpec:
    def svc(*, data: dict) -> dict:
        return data

    return ServiceSpec(
        service=svc,
        permission_classes=[IsAuthenticated] if guarded else None,
    )


def _selector_spec(*, guarded: bool = True) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda: None,
        permission_classes=[IsAuthenticated] if guarded else None,
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
            overrides={"list_orders": {"paginate": True, "ordering_fields": ["id"]}},
        )

        assert isinstance(binding, SelectorToolBinding)
        assert binding.paginate is True
        assert binding.ordering_fields == ("id",)

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
