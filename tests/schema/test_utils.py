from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field

from rest_framework import serializers

from rest_framework_mcp.schema.utils import (
    dataclass_to_schema,
    field_to_schema,
    serializer_to_schema,
)


def test_field_to_schema_boolean() -> None:
    assert field_to_schema(serializers.BooleanField()) == {"type": "boolean"}


def test_field_to_schema_integer() -> None:
    assert field_to_schema(serializers.IntegerField()) == {"type": "integer"}


def test_field_to_schema_float() -> None:
    assert field_to_schema(serializers.FloatField()) == {"type": "number"}


def test_field_to_schema_decimal() -> None:
    assert field_to_schema(serializers.DecimalField(max_digits=5, decimal_places=2)) == {
        "type": "string",
        "format": "decimal",
    }


def test_field_to_schema_datetime() -> None:
    assert field_to_schema(serializers.DateTimeField()) == {
        "type": "string",
        "format": "date-time",
    }


def test_field_to_schema_date() -> None:
    assert field_to_schema(serializers.DateField()) == {"type": "string", "format": "date"}


def test_field_to_schema_time() -> None:
    assert field_to_schema(serializers.TimeField()) == {"type": "string", "format": "time"}


def test_field_to_schema_uuid() -> None:
    assert field_to_schema(serializers.UUIDField()) == {"type": "string", "format": "uuid"}


def test_field_to_schema_email() -> None:
    assert field_to_schema(serializers.EmailField()) == {"type": "string", "format": "email"}


def test_field_to_schema_url() -> None:
    assert field_to_schema(serializers.URLField()) == {"type": "string", "format": "uri"}


def test_field_to_schema_ip() -> None:
    assert field_to_schema(serializers.IPAddressField()) == {"type": "string"}


def test_field_to_schema_json() -> None:
    assert field_to_schema(serializers.JSONField()) == {}


def test_field_to_schema_char() -> None:
    assert field_to_schema(serializers.CharField()) == {"type": "string"}


def test_field_to_schema_listfield_with_child() -> None:
    out = field_to_schema(serializers.ListField(child=serializers.IntegerField()))
    assert out == {"type": "array", "items": {"type": "integer"}}


def test_field_to_schema_listfield_no_child() -> None:
    f = serializers.ListField()
    f.child = None  # type: ignore[assignment]
    assert field_to_schema(f) == {"type": "array", "items": {}}


def test_field_to_schema_choice() -> None:
    out = field_to_schema(serializers.ChoiceField(choices=[("a", "A"), ("b", "B")]))
    assert out == {"enum": ["a", "b"]}


def test_field_to_schema_unknown_type() -> None:
    class Weird(serializers.Field):
        pass

    assert field_to_schema(Weird()) == {}


class _Inner(serializers.Serializer):
    x = serializers.IntegerField()


def test_field_to_schema_nested_serializer() -> None:
    out = field_to_schema(_Inner())
    assert out == {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def test_field_to_schema_listserializer_with_child() -> None:
    out = field_to_schema(_Inner(many=True))
    assert out == {
        "type": "array",
        "items": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    }


def test_field_to_schema_listserializer_no_child() -> None:
    ls = serializers.ListSerializer(child=_Inner())
    ls.child = None  # type: ignore[assignment]
    assert field_to_schema(ls) == {"type": "array", "items": {}}


class _Outer(serializers.Serializer):
    name = serializers.CharField(help_text="Display name", required=True)
    optional = serializers.IntegerField(required=False)
    secret = serializers.CharField(read_only=True)


def test_serializer_to_schema_required_optional_readonly() -> None:
    out = serializer_to_schema(_Outer())
    assert out["properties"]["name"]["description"] == "Display name"
    assert "secret" not in out["properties"]
    assert out["required"] == ["name"]


class _AllOptional(serializers.Serializer):
    a = serializers.IntegerField(required=False)
    b = serializers.CharField(required=False)


def test_serializer_to_schema_omits_required_key_when_all_optional() -> None:
    out = serializer_to_schema(_AllOptional())
    assert "required" not in out


@dataclass
class _DC:
    a: str
    b: int = 5
    c: list[str] = dc_field(default_factory=list)
    d: bool = False
    e: float = 1.0


def test_dataclass_to_schema_required_and_defaults() -> None:
    out = dataclass_to_schema(_DC)
    assert out["properties"]["a"] == {"type": "string"}
    assert out["properties"]["b"] == {"type": "integer"}
    assert out["properties"]["c"] == {"type": "array", "items": {"type": "string"}}
    assert out["properties"]["d"] == {"type": "boolean"}
    assert out["properties"]["e"] == {"type": "number"}
    assert out["required"] == ["a"]


@dataclass
class _DCAllRequired:
    a: str
    b: int


def test_dataclass_to_schema_no_optional_means_no_optional_in_required_list() -> None:
    out = dataclass_to_schema(_DCAllRequired)
    assert out["required"] == ["a", "b"]


@dataclass
class _DCUnknownType:
    a: dict


def test_dataclass_to_schema_unknown_annotation_falls_back_to_empty() -> None:
    out = dataclass_to_schema(_DCUnknownType)
    assert out["properties"]["a"] == {}


@dataclass
class _DCAllOptional:
    a: int = 0
    b: str = ""


def test_dataclass_to_schema_no_required() -> None:
    out = dataclass_to_schema(_DCAllOptional)
    assert "required" not in out


@dataclass
class _DCParametrisedList:
    items: list[int]


def test_dataclass_to_schema_parametrised_list() -> None:
    out = dataclass_to_schema(_DCParametrisedList)
    assert out["properties"]["items"] == {"type": "array", "items": {"type": "integer"}}
