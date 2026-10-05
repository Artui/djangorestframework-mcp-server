from __future__ import annotations

from dataclasses import dataclass

import pytest
from rest_framework import serializers
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.schema.input_schema import build_input_schema
from rest_framework_mcp.schema.output_schema import build_output_schema
from tests.testapp.serializers import InvoiceOutputSerializer


class _Ser(serializers.Serializer):
    x = serializers.IntegerField()


@dataclass
class _DC:
    name: str
    qty: int = 0


def test_build_input_schema_none_returns_empty_object() -> None:
    assert build_input_schema(None) == {"type": "object"}


def test_build_input_schema_serializer() -> None:
    assert build_input_schema(_Ser)["properties"]["x"] == {"type": "integer"}


def test_build_input_schema_dataclass() -> None:
    out = build_input_schema(_DC)
    assert out["properties"]["name"] == {"type": "string"}
    assert out["required"] == ["name"]


def test_build_input_schema_refuses_unknown_type() -> None:
    # Before drf-services 0.50 this answered ``{"type": "object"}`` --
    # byte-identical to a tool declaring no input, so the tool advertised no
    # arguments and dispatch then refused every call. The package only passes the
    # refusal through; registration refuses the shape before it gets here.
    class NotASerializer:
        pass

    with pytest.raises(TypeError, match="must be a dataclass type or a Serializer subclass"):
        build_input_schema(NotASerializer)


def test_build_output_schema_refuses_unknown_type() -> None:
    # Before drf-services 0.51 this answered ``None`` -- the answer for no output
    # declared -- while rendering the same class raised at the first call. Passed
    # through like the input side's refusal, and refused at registration first.
    class NotASerializer:
        pass

    with pytest.raises(TypeError, match="must be a BaseSerializer subclass or a dataclass type"):
        build_output_schema(NotASerializer)


def test_build_output_schema_none() -> None:
    assert build_output_schema(None) is None


def test_build_output_schema_serializer() -> None:
    out = build_output_schema(_Ser)
    assert out is not None
    assert out["type"] == "object"


def test_build_output_schema_dataclass() -> None:
    out = build_output_schema(_DC)
    assert out is not None
    assert "name" in out["properties"]


def test_build_output_schema_read_only_serializer_is_none() -> None:
    # DRF's read-only pattern renders and declares no fields, so ``None`` -- no
    # schema to advertise -- is the honest answer, and registration admits it.
    # The boundary the refusal above must not cross.
    class ReadOnly(serializers.BaseSerializer):
        def to_representation(self, instance: object) -> dict[str, object]:
            return {}

    assert build_output_schema(ReadOnly) is None


def test_build_input_schema_partial_drops_required() -> None:
    out = build_input_schema(_DC, partial=True)
    assert "required" not in out
    assert out["properties"]["name"] == {"type": "string"}


def test_build_output_schema_list_unpaginated_is_array() -> None:
    out = build_output_schema(_Ser, kind=SelectorKind.LIST)
    assert out is not None
    assert out["type"] == "array"
    assert out["items"]["type"] == "object"
    assert "x" in out["items"]["properties"]


def test_build_output_schema_list_paginated_is_envelope() -> None:
    out = build_output_schema(_Ser, kind=SelectorKind.LIST, paginate=True)
    assert out is not None
    assert out["type"] == "object"
    assert out["properties"]["items"]["type"] == "array"
    assert out["properties"]["page"] == {"type": "integer"}
    assert out["properties"]["totalPages"] == {"type": "integer"}
    assert out["properties"]["hasNext"] == {"type": "boolean"}
    assert out["required"] == ["items", "page", "totalPages", "hasNext"]


def test_build_output_schema_retrieve_kind_is_item_schema() -> None:
    out = build_output_schema(_Ser, kind=SelectorKind.RETRIEVE)
    assert out is not None
    assert out["type"] == "object"


def test_build_output_schema_list_without_serializer_is_none() -> None:
    assert build_output_schema(None, kind=SelectorKind.LIST, paginate=True) is None


# ---------- may_be_empty: a result with nothing to present ----------


def test_may_be_empty_moves_required_into_an_any_of_beside_the_empty_object() -> None:
    strict = build_output_schema(InvoiceOutputSerializer)
    admitting = build_output_schema(InvoiceOutputSerializer, may_be_empty=True)

    assert strict is not None
    assert admitting == {
        **{key: value for key, value in strict.items() if key != "required"},
        "anyOf": [{"required": strict["required"]}, {"maxProperties": 0}],
    }
    # MCP requires the root of an ``outputSchema`` to be an object schema.
    assert admitting["type"] == "object"


def test_may_be_empty_leaves_a_schema_with_nothing_required_as_derived() -> None:
    class _AllOptional(serializers.Serializer):
        note = serializers.CharField(required=False)

    assert build_output_schema(_AllOptional, may_be_empty=True) == build_output_schema(_AllOptional)


def test_may_be_empty_with_no_output_serializer_is_still_no_schema() -> None:
    assert build_output_schema(None, may_be_empty=True) is None


@pytest.mark.parametrize("paginate", [False, True], ids=["array", "envelope"])
def test_may_be_empty_returns_a_list_schema_as_derived(paginate: bool) -> None:
    # A list result is a list, empty at worst, and never nothing. The paged
    # envelope requires all four of its keys, so the rewrite would otherwise
    # move them into the ``anyOf`` and let ``{}`` pass for a page.
    derived = build_output_schema(
        InvoiceOutputSerializer, kind=SelectorKind.LIST, paginate=paginate
    )

    admitting = build_output_schema(
        InvoiceOutputSerializer, kind=SelectorKind.LIST, paginate=paginate, may_be_empty=True
    )

    assert admitting == derived
    assert derived is not None
    assert "anyOf" not in derived
