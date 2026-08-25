"""``append_agent_conventions`` — the one line that has nowhere else to go."""

from __future__ import annotations

from rest_framework import serializers
from rest_framework_services import AGENT, AgentField, build_agent_projection

from rest_framework_mcp.schema.agent_conventions import append_agent_conventions
from tests.testapp.serializers import AgentInvoiceSerializer, InvoiceOutputSerializer


def test_a_tool_with_handles_gains_the_line() -> None:
    result = append_agent_conventions(
        "Fetch an invoice.", build_agent_projection(AgentInvoiceSerializer)
    )

    assert result is not None
    assert result.startswith("Fetch an invoice.\n\n")
    assert "Identify records by `number`." in result
    assert "never read them out" in result


def test_a_tool_without_handles_is_left_alone() -> None:
    projection = build_agent_projection(InvoiceOutputSerializer)

    assert append_agent_conventions("Fetch an invoice.", projection) == "Fetch an invoice."


def test_handles_without_a_label_still_get_the_line() -> None:
    class _Handles(serializers.Serializer):
        id = serializers.IntegerField(style={AGENT: AgentField.handle()})

    result = append_agent_conventions(None, build_agent_projection(_Handles))

    assert result is not None
    assert "Identify records by" not in result
    assert result.startswith("Fields described as opaque identifiers")
