"""An entry's ``AgentContract`` reaches the binding through every registrar.

The declarations here -- ``url_kwargs``, ``query_params``, ``field_audiences``
-- are what a caller with no HTTP request has to be told, and every agent
transport needs the identical answer. They are declared once on the registry
entry; what these cases check is that the mount actually reads it, because a
shared declaration nobody reads is worse than none.

The cases go through the public surface on purpose. ``field_audiences`` was
previously exercised by constructing ``SelectorToolBinding(...)`` directly --
green while no entry point accepted it at all.
"""

from __future__ import annotations

import pytest
from rest_framework_services import (
    AgentContract,
    AgentField,
    QueryParam,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    SpecRegistry,
    UrlKwarg,
)
from rest_framework_services.types.field_audience import FieldAudience

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
            agent_contract=AgentContract(field_audiences={"sent": AgentField()}),
        )

        assert binding.agent_projection.audience("sent") is not FieldAudience.HIDDEN

    def test_register_service_tool_accepts_it(self) -> None:
        server = MCPServer(name="probe")

        binding = server.register_service_tool(
            name="send_invoice",
            spec=_service_spec(),
            agent_contract=AgentContract(field_audiences={"sent": AgentField()}),
        )

        assert binding.agent_projection.audience("sent") is not FieldAudience.HIDDEN

    def test_register_chain_tool_accepts_it(self) -> None:
        # A chain has no registry entry to inherit from, so the contract is the
        # only way in -- and it carries the one field a chain can use.
        from rest_framework_mcp.registry.types.chain_step import ChainStep

        server = MCPServer(name="probe")

        binding = server.register_chain_tool(
            name="issue_then_send",
            steps=[ChainStep(alias="issued", spec=_service_spec())],
            agent_contract=AgentContract(field_audiences={"sent": AgentField()}),
        )

        assert binding.agent_projection.audience("sent") is not FieldAudience.HIDDEN

    def test_the_serializer_still_decides_when_nothing_overrides(self) -> None:
        server = MCPServer(name="probe")

        binding = server.register_selector_tool(name="lookup", spec=_selector_spec())

        assert binding.agent_projection.audience("sent") is FieldAudience.HIDDEN

    def test_the_decorator_form_forwards_it(self) -> None:
        # The decorators are a second public surface onto the same registrars,
        # and forwarding by hand is exactly the kind of list a new argument
        # falls off -- which is how the previous defect here happened.
        server = MCPServer(name="probe")

        @server.selector_tool(
            name="lookup_invoice",
            spec=_selector_spec(),
            agent_contract=AgentContract(field_audiences={"sent": AgentField()}),
        )
        def _lookup() -> None: ...

        binding = server.tools.get("lookup_invoice")
        assert binding is not None
        assert binding.agent_projection.audience("sent") is not FieldAudience.HIDDEN


class TestRegisterSpecsCarriesTheEntrysOwn:
    def test_an_entrys_contract_reaches_the_binding_with_no_override(self) -> None:
        # The point of the whole arrangement: declared once, on the entry, and
        # the mount says nothing at all.
        registry = SpecRegistry()
        registry.register(
            "lookup_invoice",
            _selector_spec(),
            agent_contract=AgentContract(
                url_kwargs=(UrlKwarg("invoice_pk"),),
                query_params=(QueryParam("since"),),
                field_audiences={"sent": AgentField()},
            ),
        )
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(registry)

        assert binding.agent_projection.audience("sent") is not FieldAudience.HIDDEN
        assert [k.name for k in binding.url_kwargs] == ["invoice_pk"]
        assert [q.name for q in binding.query_params] == ["since"]

    def test_a_mounts_own_channels_win_over_the_entrys(self) -> None:
        registry = SpecRegistry()
        registry.register(
            "lookup_invoice",
            _selector_spec(),
            agent_contract=AgentContract(url_kwargs=(UrlKwarg("invoice_pk"),)),
        )
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(
            registry, overrides={"lookup_invoice": {"url_kwargs": (UrlKwarg("pk"),)}}
        )

        assert [k.name for k in binding.url_kwargs] == ["pk"]

    def test_overriding_the_contract_itself_replaces_it(self) -> None:
        # The only way to mount an entry with *fewer* channels than it
        # declares: an empty tuple at the mount reads as "said nothing".
        registry = SpecRegistry()
        registry.register(
            "lookup_invoice",
            _selector_spec(),
            agent_contract=AgentContract(
                url_kwargs=(UrlKwarg("invoice_pk"),),
                field_audiences={"sent": AgentField()},
            ),
        )
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(
            registry, overrides={"lookup_invoice": {"agent_contract": AgentContract()}}
        )

        assert binding.url_kwargs == ()
        assert binding.agent_projection.audience("sent") is FieldAudience.HIDDEN

    def test_an_entry_with_no_contract_registers_unchanged(self) -> None:
        registry = SpecRegistry()
        registry.register("lookup_invoice", _selector_spec())
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(registry)

        assert binding.url_kwargs == ()
        assert binding.agent_projection.audience("sent") is FieldAudience.HIDDEN


class TestItStillValidates:
    def test_two_labels_raise_naming_the_tool_on_first_use(self) -> None:
        # The guarantee the recipe makes right after the snippet, checked
        # through the entry point rather than on the dataclass.
        #
        # It raises on first use, not at registration: ``agent_projection`` is a
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
            agent_contract=AgentContract(field_audiences={"id": AgentField.label()}),
        )

        with pytest.raises(ImproperlyConfigured, match="lookup_invoice"):
            _ = binding.agent_projection
