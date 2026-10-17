"""An entry's ``OfflineContract`` reaches the binding through every registrar.

The declarations here -- ``url_kwargs``, ``query_params``, ``field_audiences``
-- are what a caller with no HTTP request has to be told, and every agent
transport needs the identical answer. They are declared once, as the ``reads``
and ``field_audiences`` of the spec a registry entry holds, which the entry's
``agent_contract`` answers; what these cases check is that the mount actually
reads it, because a shared declaration nobody reads is worse than none.

The cases go through the public surface on purpose. ``field_audiences`` was
previously exercised by constructing ``SelectorToolBinding(...)`` directly --
green while no entry point accepted it at all.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django_service_specs import FieldAudience, FieldMarking, SelectorKind, SpecRegistry
from rest_framework_services import (
    OfflineContract,
    QueryParam,
    SelectorSpec,
    ServiceSpec,
    UrlKwarg,
)

from rest_framework_mcp.server.mcp_server import MCPServer
from tests.testapp.serializers import AgentInvoiceSerializer


def _selector_spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda **_: [],
        output_serializer=AgentInvoiceSerializer,
    )


def _service_spec() -> ServiceSpec:
    return ServiceSpec(
        service=lambda **_: None,
        output_selector_spec=_selector_spec(),
    )


class TestTheRegistrarsReadIt:
    """The shape ``docs/recipes/agent-audience.md`` shows."""

    def test_register_selector_tool_accepts_it(self) -> None:
        server = MCPServer(name="probe")

        binding = server.register_selector_tool(
            name="lookup_invoice",
            spec=_selector_spec(),
            agent_contract=OfflineContract(field_audiences={"sent": FieldMarking()}),
        )

        assert binding.audience_projection.audience("sent") is not FieldAudience.HIDDEN

    def test_register_service_tool_accepts_it(self) -> None:
        server = MCPServer(name="probe")

        binding = server.register_service_tool(
            name="send_invoice",
            spec=_service_spec(),
            agent_contract=OfflineContract(field_audiences={"sent": FieldMarking()}),
        )

        assert binding.audience_projection.audience("sent") is not FieldAudience.HIDDEN

    def test_register_chain_tool_accepts_it(self) -> None:
        # A chain has no registry entry to inherit from, so the contract is the
        # only way in -- and it carries the one field a chain can use.
        from rest_framework_mcp.registry.types.chain_step import ChainStep

        server = MCPServer(name="probe")

        binding = server.register_chain_tool(
            name="issue_then_send",
            steps=[ChainStep(alias="issued", spec=_service_spec())],
            agent_contract=OfflineContract(field_audiences={"sent": FieldMarking()}),
        )

        assert binding.audience_projection.audience("sent") is not FieldAudience.HIDDEN

    def test_the_serializer_still_decides_when_nothing_overrides(self) -> None:
        server = MCPServer(name="probe")

        binding = server.register_selector_tool(name="lookup", spec=_selector_spec())

        assert binding.audience_projection.audience("sent") is FieldAudience.HIDDEN

    def test_the_decorator_form_forwards_it(self) -> None:
        # The decorators are a second public surface onto the same registrars,
        # and forwarding by hand is exactly the kind of list a new argument
        # falls off -- which is how the previous defect here happened.
        server = MCPServer(name="probe")

        @server.selector_tool(
            name="lookup_invoice",
            spec=_selector_spec(),
            agent_contract=OfflineContract(field_audiences={"sent": FieldMarking()}),
        )
        def _lookup() -> None: ...

        binding = server.tools.get("lookup_invoice")
        assert binding is not None
        assert binding.audience_projection.audience("sent") is not FieldAudience.HIDDEN


def _registry(**declared: Any) -> SpecRegistry:
    """A registry holding ``lookup_invoice``, its spec re-declared with ``declared``.

    The form ``docs/recipes/register-from-spec-registry.md`` shows: since
    drf-services 0.58.0 ``SpecRegistry.register`` takes no ``agent_contract=``,
    and an entry's contract is what its spec's ``reads`` and ``field_audiences``
    declare, so an entry already registered is re-declared with
    ``dataclasses.replace``. ``permission_classes=[]`` because the registry
    refuses a spec declaring no permissions at all.
    """
    registry = SpecRegistry()
    registry.register("lookup_invoice", replace(_selector_spec(), permission_classes=[]))
    entry = registry.get("lookup_invoice")
    assert entry is not None
    if not declared:
        return registry
    return SpecRegistry([replace(entry, spec=replace(entry.spec, **declared))])


def _advertised(server: MCPServer) -> list[str]:
    """The argument names ``tools/list`` serves for the one registered tool."""
    listing = server.list_tools(user=AnonymousUser())
    assert isinstance(listing, dict)
    (tool,) = listing["tools"]
    return sorted(tool["inputSchema"].get("properties", {}))


class TestRegisterSpecsCarriesTheEntrysOwn:
    def test_an_entrys_contract_reaches_the_binding_with_no_override(self) -> None:
        # The point of the whole arrangement: declared once, on the spec the
        # entry holds, and the mount says nothing at all.
        registry = _registry(
            reads=[UrlKwarg("invoice_pk"), QueryParam("since")],
            field_audiences={"sent": FieldMarking()},
        )
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(registry)

        assert binding.audience_projection.audience("sent") is not FieldAudience.HIDDEN
        assert [k.name for k in binding.url_kwargs] == ["invoice_pk"]
        assert [q.name for q in binding.query_params] == ["since"]
        assert _advertised(server) == ["invoice_pk", "since"]

    def test_a_spec_declaring_its_contract_at_construction_reaches_the_binding(self) -> None:
        # The first form both recipes show: the declaration made where the spec
        # is built, before any registry sees it.
        registry = SpecRegistry()
        registry.register(
            "lookup_invoice",
            SelectorSpec(
                kind=SelectorKind.LIST,
                selector=lambda **_: [],
                output_serializer=AgentInvoiceSerializer,
                permission_classes=[],
                reads=[UrlKwarg("invoice_pk", type="integer")],
                field_audiences={"sent": FieldMarking()},
            ),
        )
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(registry)

        assert binding.audience_projection.audience("sent") is not FieldAudience.HIDDEN
        assert [(k.name, k.type) for k in binding.url_kwargs] == [("invoice_pk", "integer")]
        assert _advertised(server) == ["invoice_pk"]

    def test_a_mounts_own_channels_win_over_the_entrys(self) -> None:
        registry = _registry(reads=[UrlKwarg("invoice_pk")])
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(
            registry, overrides={"lookup_invoice": {"url_kwargs": (UrlKwarg("pk"),)}}
        )

        assert [k.name for k in binding.url_kwargs] == ["pk"]
        assert _advertised(server) == ["pk"]

    def test_overriding_the_contract_itself_replaces_it(self) -> None:
        # An empty tuple at the mount reads as "said nothing", so replacing the
        # contract is how a mount advertises fewer channels than the spec reads.
        registry = _registry(
            reads=[UrlKwarg("invoice_pk")],
            field_audiences={"sent": FieldMarking()},
        )
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(
            registry, overrides={"lookup_invoice": {"agent_contract": OfflineContract()}}
        )

        assert binding.url_kwargs == ()
        assert binding.audience_projection.audience("sent") is FieldAudience.HIDDEN
        assert _advertised(server) == []

    def test_an_entry_with_no_contract_registers_unchanged(self) -> None:
        registry = _registry()
        entry = registry.get("lookup_invoice")
        assert entry is not None
        assert entry.agent_contract is None
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(registry)

        assert binding.url_kwargs == ()
        assert binding.audience_projection.audience("sent") is FieldAudience.HIDDEN


class TestItStillValidates:
    def test_two_labels_raise_naming_the_tool_on_first_use(self) -> None:
        # The guarantee the recipe makes right after the snippet, checked
        # through the entry point rather than on the dataclass.
        #
        # It raises on first use, not at registration: ``audience_projection`` is a
        # cached_property, so a mistyped override survives startup and breaks a
        # request instead. Deliberate -- resolving a serializer eagerly at
        # binding construction would run before the app registry is necessarily
        # ready -- so the recipe says when rather than implying startup.
        from django.core.exceptions import ImproperlyConfigured

        server = MCPServer(name="probe")
        binding = server.register_selector_tool(
            name="lookup_invoice",
            spec=_selector_spec(),
            # ``number`` is already the serializer's label, so claiming it for
            # ``id`` as well leaves two -- the clash the recipe promises is
            # caught. Overriding ``number`` too would merely *move* the label.
            agent_contract=OfflineContract(field_audiences={"id": FieldMarking.label()}),
        )

        with pytest.raises(ImproperlyConfigured, match="lookup_invoice"):
            _ = binding.audience_projection
