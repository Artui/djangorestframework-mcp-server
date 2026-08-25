"""Per-tool ``field_audiences`` overrides on top of the serializer's markings."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import AgentField, SelectorKind, SelectorSpec
from rest_framework_services.types.field_audience import FieldAudience

from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from tests.testapp.serializers import AgentInvoiceSerializer


def _binding(**kwargs: object) -> SelectorToolBinding:
    return SelectorToolBinding(
        name="invoices",
        description=None,
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda **_: [],
            output_serializer=AgentInvoiceSerializer,
        ),
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_serializer_alone_decides_by_default() -> None:
    projection = _binding().agent_projection

    assert projection.audience("sent") is FieldAudience.HIDDEN
    assert projection.label == "number"


def test_an_override_can_un_hide_what_a_sibling_tool_drops() -> None:
    """The case the override exists for: a lookup tool needs what its neighbour hides."""
    projection = _binding(field_audiences={"sent": AgentField()}).agent_projection

    assert projection.audience("sent") is FieldAudience.CONTENT
    # Everything not overridden still comes from the serializer.
    assert projection.audience("id") is FieldAudience.HANDLE
    assert projection.choice_labels["status"]["PAID"] == "Paid"


def test_an_override_can_move_the_label() -> None:
    projection = _binding(
        field_audiences={"number": AgentField(), "id": AgentField.label()}
    ).agent_projection

    assert projection.label == "id"


def test_an_override_that_leaves_two_labels_raises() -> None:
    binding = _binding(field_audiences={"id": AgentField.label()})

    with pytest.raises(ImproperlyConfigured, match="A record has one name"):
        _ = binding.agent_projection


def test_the_projection_is_derived_once_per_binding() -> None:
    binding = _binding()

    assert binding.agent_projection is binding.agent_projection
