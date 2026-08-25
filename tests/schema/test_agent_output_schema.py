"""``build_output_schema`` — where the agent projection lands for each kind."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework_services import AGENT, AgentField, build_agent_projection
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.schema.agent_conventions import HANDLE_DESCRIPTION
from rest_framework_mcp.schema.output_schema import build_output_schema
from tests.testapp.serializers import AgentInvoiceSerializer, InvoiceOutputSerializer

PROJECTION = build_agent_projection(AgentInvoiceSerializer)


def test_retrieve_schema_is_annotated_directly() -> None:
    schema: Any = build_output_schema(AgentInvoiceSerializer, projection=PROJECTION)

    assert "sent" not in schema["properties"]
    assert schema["properties"]["id"]["description"] == "Invoice handle."


def test_list_schema_is_annotated_inside_the_array() -> None:
    schema: Any = build_output_schema(
        AgentInvoiceSerializer, kind=SelectorKind.LIST, projection=PROJECTION
    )

    assert schema["type"] == "array"
    assert "sent" not in schema["items"]["properties"]


def test_paginated_schema_is_annotated_inside_the_envelope() -> None:
    """The envelope's own keys belong to this transport, not to a serializer."""
    schema: Any = build_output_schema(
        AgentInvoiceSerializer, kind=SelectorKind.LIST, paginate=True, projection=PROJECTION
    )
    item = schema["properties"]["items"]["items"]

    assert "sent" not in item["properties"]
    assert set(schema["properties"]) == {"items", "page", "totalPages", "hasNext"}
    assert schema["required"] == ["items", "page", "totalPages", "hasNext"]


def test_an_empty_projection_leaves_the_schema_alone() -> None:
    projection = build_agent_projection(InvoiceOutputSerializer)
    schema: Any = build_output_schema(InvoiceOutputSerializer, projection=projection)

    assert schema == build_output_schema(InvoiceOutputSerializer)


def test_no_serializer_yields_no_schema() -> None:
    assert build_output_schema(None, projection=PROJECTION) is None


def test_an_unlabelled_handle_gets_this_transport_s_wording() -> None:
    """The sentence is a prompt, so it is supplied here, not upstream.

    drf-services holds the markings and no wording at all — it does not know a
    model is what reads the schema. This asserts the two halves are joined up.
    """

    class _Thing(serializers.Serializer):
        ref = serializers.CharField(style={AGENT: AgentField.handle()})
        described = serializers.CharField(style={AGENT: AgentField.handle("A widget handle.")})

    schema: Any = build_output_schema(_Thing, projection=build_agent_projection(_Thing))

    assert schema["properties"]["ref"]["description"] == HANDLE_DESCRIPTION
    # A handle that says what it is keeps its own words.
    assert schema["properties"]["described"]["description"] == "A widget handle."
