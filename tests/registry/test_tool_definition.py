from __future__ import annotations

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import (
    ArgumentBinding,
    OutputFormat,
    ToolKind,
    UnknownArguments,
)
from rest_framework_mcp.registry.types.tool_definition import ToolDefinition


def _svc() -> None:
    return None


def _sel() -> list[dict[str, str]]:
    return []


def test_service_classmethod_sets_kind_service() -> None:
    spec = ServiceSpec(service=_svc, atomic=False)
    d = ToolDefinition.service(name="t", spec=spec)
    assert d.kind is ToolKind.SERVICE
    assert d.name == "t"
    assert d.spec is spec


def test_selector_classmethod_sets_kind_selector() -> None:
    spec = SelectorSpec(selector=_sel)
    d = ToolDefinition.selector(name="x", spec=spec)
    assert d.kind is ToolKind.SELECTOR
    assert d.name == "x"
    assert d.spec is spec


def test_service_classmethod_forwards_all_kwargs() -> None:
    d = ToolDefinition.service(
        name="t",
        spec=ServiceSpec(service=_svc, atomic=False),
        description="desc",
        title="Title",
        output_format=OutputFormat.TOON,
        include_structured_content=True,
        argument_binding=ArgumentBinding.MERGE,
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )
    assert d.description == "desc"
    assert d.title == "Title"
    assert d.output_format is OutputFormat.TOON
    assert d.include_structured_content is True
    assert d.argument_binding is ArgumentBinding.MERGE
    assert d.unknown_arguments is UnknownArguments.PASSTHROUGH


def test_service_classmethod_forwards_include_output_schema() -> None:
    d = ToolDefinition.service(
        name="t",
        spec=ServiceSpec(service=_svc, atomic=False),
        include_output_schema=False,
    )
    assert d.include_output_schema is False


def test_selector_classmethod_forwards_include_output_schema() -> None:
    d = ToolDefinition.selector(
        name="x",
        spec=SelectorSpec(selector=_sel),
        include_output_schema=True,
    )
    assert d.include_output_schema is True


def test_selector_classmethod_forwards_all_selector_kwargs() -> None:
    d = ToolDefinition.selector(
        name="x",
        spec=SelectorSpec(selector=_sel),
        input_serializer=str,
        ordering_fields=("a",),
        paginate=True,
        argument_binding=ArgumentBinding.REPLACE,
    )
    assert d.input_serializer is str
    assert d.ordering_fields == ("a",)
    assert d.paginate is True
    assert d.argument_binding is ArgumentBinding.REPLACE


def test_default_optional_fields_are_none() -> None:
    """Unspecified kwargs land as ``None`` so :func:`register_tools` treats them as no-override."""
    d = ToolDefinition.service(name="t", spec=ServiceSpec(service=_svc, atomic=False))
    assert d.description is None
    assert d.output_format is None
    assert d.argument_binding is None
    assert d.unknown_arguments is None
