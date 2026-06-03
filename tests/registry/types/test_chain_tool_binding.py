"""Unit coverage for ``ChainToolBinding`` validation + derived properties."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from tests.testapp.serializers import InvoiceInputSerializer, InvoiceOutputSerializer


def _svc_spec(**kw: Any) -> ServiceSpec:
    return ServiceSpec(service=lambda **_: {}, atomic=False, **kw)


def _sel_spec(**kw: Any) -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None, **kw)


def _binding(steps: list[ChainStep], **kw: Any) -> ChainToolBinding:
    return ChainToolBinding(name="c", description=None, steps=tuple(steps), **kw)


def test_requires_at_least_one_step() -> None:
    with pytest.raises(ImproperlyConfigured, match="at least one step"):
        _binding([])


def test_rejects_duplicate_aliases() -> None:
    with pytest.raises(ImproperlyConfigured, match="duplicate step alias"):
        _binding([ChainStep("a", _svc_spec()), ChainStep("a", _svc_spec())])


def test_rejects_non_spec_step() -> None:
    with pytest.raises(ImproperlyConfigured, match="must be a ServiceSpec or SelectorSpec"):
        _binding([ChainStep("a", object())])  # type: ignore[arg-type]


def test_rejects_selector_step_without_selector() -> None:
    with pytest.raises(ImproperlyConfigured, match="has no selector"):
        _binding([ChainStep("a", SelectorSpec(kind=SelectorKind.LIST))])


def test_rejects_output_all_with_output_alias() -> None:
    with pytest.raises(ImproperlyConfigured, match="output_all=True is incompatible"):
        _binding([ChainStep("a", _svc_spec())], output_all=True, output_alias="a")


def test_rejects_unknown_output_alias() -> None:
    with pytest.raises(ImproperlyConfigured, match="not a known step alias"):
        _binding([ChainStep("a", _svc_spec())], output_alias="nope")


def test_rejects_output_schema_without_structured_content() -> None:
    with pytest.raises(ImproperlyConfigured, match="incompatible"):
        _binding(
            [ChainStep("a", _svc_spec())],
            include_output_schema=True,
            include_structured_content=False,
        )


def test_output_step_defaults_to_last() -> None:
    b = _binding([ChainStep("a", _svc_spec()), ChainStep("b", _svc_spec())])
    assert b.output_step.alias == "b"


def test_output_step_honors_alias() -> None:
    b = _binding([ChainStep("a", _svc_spec()), ChainStep("b", _svc_spec())], output_alias="a")
    assert b.output_step.alias == "a"


def test_resolved_input_serializer_explicit_wins() -> None:
    b = _binding([ChainStep("a", _svc_spec())], input_serializer=InvoiceInputSerializer)
    assert b.resolved_input_serializer is InvoiceInputSerializer


def test_resolved_input_serializer_falls_back_to_first_service_step() -> None:
    b = _binding([ChainStep("a", _svc_spec(input_serializer=InvoiceInputSerializer))])
    assert b.resolved_input_serializer is InvoiceInputSerializer


def test_resolved_input_serializer_none_for_selector_first_step() -> None:
    b = _binding([ChainStep("a", _sel_spec())])
    assert b.resolved_input_serializer is None


def test_output_serializer_none_under_output_all() -> None:
    b = _binding([ChainStep("a", _svc_spec())], output_all=True)
    assert b.output_serializer is None


def test_output_serializer_from_service_output_selector_spec() -> None:
    spec = _svc_spec(
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer
        )
    )
    b = _binding([ChainStep("a", spec)])
    assert b.output_serializer is InvoiceOutputSerializer


def test_output_serializer_none_for_service_without_output_selector_spec() -> None:
    b = _binding([ChainStep("a", _svc_spec())])
    assert b.output_serializer is None


def test_output_serializer_from_selector_step() -> None:
    spec = _sel_spec(output_serializer=InvoiceOutputSerializer)
    b = _binding([ChainStep("a", spec)])
    assert b.output_serializer is InvoiceOutputSerializer
