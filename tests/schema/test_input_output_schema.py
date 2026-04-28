from __future__ import annotations

from dataclasses import dataclass

from rest_framework import serializers

from rest_framework_mcp.schema.input_schema import build_input_schema
from rest_framework_mcp.schema.output_schema import build_output_schema


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


def test_build_input_schema_unknown_type_falls_back() -> None:
    class NotASerializer:
        pass

    assert build_input_schema(NotASerializer) == {"type": "object"}


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


def test_build_output_schema_unknown_type() -> None:
    class NotASerializer:
        pass

    assert build_output_schema(NotASerializer) is None
