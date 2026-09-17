"""``build_service_tool_input_schema`` for a ``many=True`` spec.

The list travels under ``spec.many_argument``, so the advertised input is an object
with that one array property. The shape is drf-services' ``spec_to_json_schema``,
taken rather than rebuilt, and the URL kwargs and query params merge in beside it
as they do beside a single item's fields.

Bindings are built directly rather than through the adapter, so each test reads
the schema a binding advertises, whatever registration would have said about it.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework_services import spec_to_json_schema
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import QueryParam, UrlKwarg
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.schema.input_schema import build_input_schema
from rest_framework_mcp.schema.service_tool_schema import build_service_tool_input_schema
from tests.testapp.serializers import InvoiceInputSerializer


def _binding(spec: ServiceSpec, **kwargs: Any) -> ToolBinding:
    return ToolBinding(name="bulk", description=None, spec=spec, **kwargs)


def _bulk(**overrides: Any) -> ServiceSpec:
    declared: dict[str, Any] = {"input_serializer": InvoiceInputSerializer, **overrides}
    return ServiceSpec(service=lambda **_: None, atomic=False, many=True, **declared)


def test_the_list_is_advertised_under_the_default_argument() -> None:
    schema = build_service_tool_input_schema(_binding(_bulk()))

    assert schema == {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": build_input_schema(InvoiceInputSerializer)}
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def test_a_declared_argument_name_is_the_property_advertised() -> None:
    schema = build_service_tool_input_schema(_binding(_bulk(many_argument="rows")))

    assert list(schema["properties"]) == ["rows"]
    assert schema["required"] == ["rows"]


class _NonEmptyInvoices(InvoiceInputSerializer):
    @classmethod
    def many_init(cls, *args: Any, **kwargs: Any) -> Any:
        kwargs["allow_empty"] = False
        kwargs["max_length"] = 50
        return super().many_init(*args, **kwargs)


def test_the_shape_is_drf_services_reflection_verbatim() -> None:
    """Bounds DRF decides in ``many_init`` reach the schema, which only reading the
    list serializer dispatch builds can see; a wrapper assembled here from the item
    schema would advertise an unbounded list."""
    spec = _bulk(input_serializer=_NonEmptyInvoices)

    schema = build_service_tool_input_schema(_binding(spec))

    assert schema == spec_to_json_schema(spec, phase="input")
    assert schema["properties"]["items"]["minItems"] == 1
    assert schema["properties"]["items"]["maxItems"] == 50


def test_url_kwargs_and_query_params_merge_in_beside_the_list() -> None:
    binding = _binding(
        _bulk(),
        url_kwargs=(UrlKwarg("project_pk", required=True),),
        query_params=(QueryParam("dry_run", type="boolean"),),
    )

    schema = build_service_tool_input_schema(binding)

    assert list(schema["properties"]) == ["items", "project_pk", "dry_run"]
    assert schema["properties"]["items"]["type"] == "array"
    assert schema["properties"]["project_pk"] == UrlKwarg("project_pk", required=True).json_schema()
    assert schema["properties"]["dry_run"] == {"type": "boolean"}
    assert schema["required"] == ["items", "project_pk"]


class _Optional(serializers.Serializer):
    note = serializers.CharField()


def test_partial_relaxes_the_item_and_never_the_argument() -> None:
    """``spec.partial`` drops ``required`` inside each item; the list itself is still
    required, as dispatch refuses a call without it under ``partial`` too."""
    schema = build_service_tool_input_schema(
        _binding(_bulk(input_serializer=_Optional, partial=True))
    )

    assert schema["required"] == ["items"]
    assert "required" not in schema["properties"]["items"]["items"]
