"""``append_agent_conventions`` — the one line that has nowhere else to go."""

from __future__ import annotations

from rest_framework import serializers
from rest_framework_services import MARKING, FieldMarking, build_audience_projection

from rest_framework_mcp.schema.agent_conventions import append_agent_conventions
from tests.testapp.serializers import AgentInvoiceSerializer, InvoiceOutputSerializer


def test_a_tool_with_handles_gains_the_line() -> None:
    result = append_agent_conventions(
        "Fetch an invoice.", build_audience_projection(AgentInvoiceSerializer)
    )

    assert result is not None
    assert result.startswith("Fetch an invoice.\n\n")
    assert "Identify records by `number`." in result
    # The advice the Pydantic-AI toolset gives; it was "never read them out".
    assert result.endswith(
        "pass them to other tools that ask for one; refer to records by their "
        "name in anything you say, never by the identifier."
    )


def test_a_tool_without_handles_is_left_alone() -> None:
    projection = build_audience_projection(InvoiceOutputSerializer)

    assert append_agent_conventions("Fetch an invoice.", projection) == "Fetch an invoice."


def test_handles_without_a_label_still_get_the_line() -> None:
    class _Handles(serializers.Serializer):
        id = serializers.IntegerField(style={MARKING: FieldMarking.handle()})

    result = append_agent_conventions(None, build_audience_projection(_Handles))

    assert result is not None
    assert "Identify records by" not in result
    assert result.startswith("Fields described as opaque identifiers")


def test_the_line_is_the_one_passed() -> None:
    """A server's ``AgentConventions.handle_line`` arrives here as text."""
    result = append_agent_conventions(
        "Fetch an invoice.",
        build_audience_projection(AgentInvoiceSerializer),
        handle_line="Use the ids for calls.",
    )

    # The label prefix is this transport's framing and stays.
    assert result == "Fetch an invoice.\n\nIdentify records by `number`. Use the ids for calls."


def test_none_drops_the_line_and_its_prefix() -> None:
    projection = build_audience_projection(AgentInvoiceSerializer)

    assert append_agent_conventions("Fetch an invoice.", projection, handle_line=None) == (
        "Fetch an invoice."
    )
    assert append_agent_conventions(None, projection, handle_line=None) is None
