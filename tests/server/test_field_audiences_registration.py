"""``field_audiences`` reaches the binding through the routes the docs describe.

The capability shipped on the three tool bindings and the recipe documented it
as a registration argument, but no entry point accepted it -- the documented call
raised ``TypeError``.

CI was green because the only test constructed ``SelectorToolBinding(...)``
directly, bypassing every entry point. The feature was exercised on the object
the docs never mention and unreachable through the API the docs do. So these
cases go through the public surface on purpose: a test that instantiates past it
cannot fail when it is missing.
"""

from __future__ import annotations

import pytest
from rest_framework_services import AgentField, SelectorKind, SelectorSpec, ServiceSpec
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


class TestTheDocumentedCall:
    """The exact shape ``docs/recipes/agent-audience.md`` shows."""

    def test_register_selector_tool_accepts_it(self) -> None:
        server = MCPServer(name="probe")

        binding = server.register_selector_tool(
            name="lookup_invoice",
            spec=_selector_spec(),
            field_audiences={"sent": AgentField()},
        )

        assert binding.agent_projection.audience("sent") is not FieldAudience.HIDDEN

    def test_register_service_tool_accepts_it(self) -> None:
        server = MCPServer(name="probe")

        binding = server.register_service_tool(
            name="send_invoice",
            spec=_service_spec(),
            field_audiences={"sent": AgentField()},
        )

        assert binding.agent_projection.audience("sent") is not FieldAudience.HIDDEN

    def test_the_serializer_still_decides_when_nothing_overrides(self) -> None:
        server = MCPServer(name="probe")

        binding = server.register_selector_tool(name="lookup", spec=_selector_spec())

        assert binding.agent_projection.audience("sent") is FieldAudience.HIDDEN


class TestTheOtherRoutesIn:
    def test_the_decorator_form_forwards_it(self) -> None:
        # The decorators are a second public surface onto the same registrars,
        # and forwarding by hand is exactly the kind of list a new kwarg falls
        # off -- which is how this defect happened in the first place.
        server = MCPServer(name="probe")

        @server.selector_tool(
            name="lookup_invoice",
            spec=_selector_spec(),
            field_audiences={"sent": AgentField()},
        )
        def _lookup() -> None: ...

        binding = server.tools.get("lookup_invoice")
        assert binding is not None
        assert binding.agent_projection.audience("sent") is not FieldAudience.HIDDEN

    def test_register_specs_takes_it_as_an_override_key(self) -> None:
        # No plumbing of its own: override keys are checked against the target
        # method's signature, so wiring the registrars is what made this work.
        from rest_framework_services import SpecRegistry

        registry = SpecRegistry()
        registry.register("lookup_invoice", _selector_spec())
        server = MCPServer(name="probe")

        (binding,) = server.register_specs(
            registry,
            overrides={"lookup_invoice": {"field_audiences": {"sent": AgentField()}}},
        )

        assert binding.agent_projection.audience("sent") is not FieldAudience.HIDDEN


class TestItStillValidates:
    def test_two_labels_raise_naming_the_tool_on_first_use(self) -> None:
        # The guarantee the recipe makes right after the snippet, checked
        # through the entry point rather than on the dataclass.
        #
        # It raises on first use, not at registration: ``agent_projection`` is a
        # cached_property, so a mistyped override survives startup and breaks a
        # request instead. Deliberate -- resolving a serializer eagerly at
        # binding construction would run before the app registry is necessarily
        # ready -- so the recipe now says when rather than implying startup.
        from django.core.exceptions import ImproperlyConfigured

        server = MCPServer(name="probe")
        binding = server.register_selector_tool(
            name="lookup_invoice",
            spec=_selector_spec(),
            # ``number`` is already the serializer's label, so claiming it for
            # ``id`` as well leaves two -- the clash the recipe promises is
            # caught. Overriding ``number`` too would merely *move* the label.
            field_audiences={"id": AgentField.label()},
        )

        with pytest.raises(ImproperlyConfigured, match="lookup_invoice"):
            _ = binding.agent_projection
