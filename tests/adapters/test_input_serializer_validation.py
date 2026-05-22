"""Registration-time checks for input_serializer ↔ callable parameter parity.

``rest_framework_mcp.adapters.utils.validate_input_serializer_against_callable``
runs from both :func:`selector_spec_to_tool` and :func:`service_spec_to_tool`
so that a config bug — a serializer field that the dispatched callable
won't actually accept — surfaces at registration rather than the first
client call. Tests exercise the helper directly (signal shape) plus the
two adapter entry points (wiring).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.adapters.selector_to_tool import selector_spec_to_tool
from rest_framework_mcp.adapters.service_to_tool import service_spec_to_tool
from rest_framework_mcp.adapters.utils import validate_input_serializer_against_callable
from rest_framework_mcp.constants import ArgumentBinding


class _TwoField(drf_serializers.Serializer):
    expand = drf_serializers.BooleanField()
    detail = drf_serializers.CharField()


# ---------- direct unit tests of the helper ----------


def test_skips_when_serializer_is_none() -> None:
    """Nothing to validate ⇒ no error."""
    validate_input_serializer_against_callable(
        label="x",
        input_serializer=None,
        callable_=lambda **kw: None,
        argument_binding=ArgumentBinding.MERGE,
    )


def test_skips_when_callable_is_none() -> None:
    """``selector=None`` is caught elsewhere — this helper just no-ops."""
    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_TwoField,
        callable_=None,
        argument_binding=ArgumentBinding.MERGE,
    )


def test_merge_passes_when_callable_declares_every_field_as_param() -> None:
    def fn(*, expand: bool, detail: str) -> None: ...

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_TwoField,
        callable_=fn,
        argument_binding=ArgumentBinding.MERGE,
    )


def test_merge_passes_when_callable_accepts_var_keyword() -> None:
    def fn(**kwargs: Any) -> None: ...

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_TwoField,
        callable_=fn,
        argument_binding=ArgumentBinding.MERGE,
    )


def test_merge_passes_when_callable_declares_data_bundle_param() -> None:
    """``data=`` carries the full validated payload — fields needn't map."""

    def fn(*, data: dict[str, Any]) -> None: ...

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_TwoField,
        callable_=fn,
        argument_binding=ArgumentBinding.MERGE,
    )


def test_merge_raises_when_fields_missing_from_callable() -> None:
    def fn(*, expand: bool) -> None: ...  # missing ``detail``

    with pytest.raises(ImproperlyConfigured) as excinfo:
        validate_input_serializer_against_callable(
            label="my tool",
            input_serializer=_TwoField,
            callable_=fn,
            argument_binding=ArgumentBinding.MERGE,
        )
    msg = str(excinfo.value)
    assert "my tool" in msg
    assert "detail" in msg


def test_merge_exempts_reserved_pool_seeds_and_post_fetch_keys() -> None:
    """A serializer that re-declares ``request`` / ``ordering`` etc.
    isn't flagged — the dispatch pipeline strips those from the spread,
    so they never reach the callable as kwargs.
    """

    class _ReservedNames(drf_serializers.Serializer):
        request = drf_serializers.CharField()  # pool seed
        ordering = drf_serializers.CharField()  # selector post-fetch

    def fn(**_kw: Any) -> None: ...

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_ReservedNames,
        callable_=fn,
        argument_binding=ArgumentBinding.MERGE,
    )


def test_data_only_passes_when_callable_declares_data() -> None:
    def fn(*, data: Any) -> None: ...

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_TwoField,
        callable_=fn,
        argument_binding=ArgumentBinding.DATA_ONLY,
    )


def test_data_only_passes_when_callable_accepts_var_keyword() -> None:
    def fn(**kwargs: Any) -> None: ...

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_TwoField,
        callable_=fn,
        argument_binding=ArgumentBinding.DATA_ONLY,
    )


def test_data_only_raises_when_callable_lacks_data() -> None:
    def fn(*, expand: bool, detail: str) -> None: ...

    with pytest.raises(ImproperlyConfigured) as excinfo:
        validate_input_serializer_against_callable(
            label="x",
            input_serializer=_TwoField,
            callable_=fn,
            argument_binding=ArgumentBinding.DATA_ONLY,
        )
    assert "DATA_ONLY" in str(excinfo.value)
    assert "data" in str(excinfo.value)


# ---------- dataclass input_serializer ----------


@dataclass
class _DCInput:
    foo: str
    bar: int


def test_dataclass_serializer_field_names_extracted() -> None:
    def fn(*, foo: str) -> None: ...  # missing bar

    with pytest.raises(ImproperlyConfigured, match="bar"):
        validate_input_serializer_against_callable(
            label="x",
            input_serializer=_DCInput,
            callable_=fn,
            argument_binding=ArgumentBinding.MERGE,
        )


def test_non_serializer_non_dataclass_input_yields_no_violations() -> None:
    """``input_serializer`` that isn't a Serializer subclass or @dataclass
    leaves the helper with no fields to validate against. Better to
    no-op than to fabricate a false positive.
    """

    class _NotASerializer:
        pass

    def fn() -> None: ...

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_NotASerializer,
        callable_=fn,
        argument_binding=ArgumentBinding.MERGE,
    )


# ---------- wiring through the adapters ----------


def test_selector_spec_to_tool_runs_the_check() -> None:
    def selector(*, user: Any) -> list[Any]: ...  # noqa: ARG001

    with pytest.raises(ImproperlyConfigured, match="selector tool 'invoices.list'"):
        selector_spec_to_tool(
            name="invoices.list",
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
            input_serializer=_TwoField,
        )


def test_service_spec_to_tool_runs_the_check() -> None:
    def service() -> None: ...

    with pytest.raises(ImproperlyConfigured, match="service tool 't'"):
        service_spec_to_tool(
            name="t",
            spec=ServiceSpec(service=service, input_serializer=_TwoField, atomic=False),
            argument_binding=ArgumentBinding.MERGE,
        )


# ---------- required-callable-param coverage (reverse direction) ----------


class _OneField(drf_serializers.Serializer):
    project_id = drf_serializers.CharField()


def test_required_param_must_appear_in_serializer() -> None:
    """``def fn(*, user, project_id, region)`` with a serializer that only
    declares ``project_id`` raises — ``region`` has no source.
    """

    def fn(*, user: Any, project_id: str, region: str) -> None: ...  # noqa: ARG001

    with pytest.raises(ImproperlyConfigured) as excinfo:
        validate_input_serializer_against_callable(
            label="my tool",
            input_serializer=_OneField,
            callable_=fn,
            argument_binding=ArgumentBinding.MERGE,
        )
    msg = str(excinfo.value)
    assert "region" in msg
    # The error lists the available sources to help the user fix it.
    assert "project_id" in msg


def test_required_param_satisfied_by_pool_seed() -> None:
    """``user`` is a pool seed — never needs to be in the serializer."""

    def fn(*, user: Any, project_id: str) -> None: ...  # noqa: ARG001

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_OneField,
        callable_=fn,
        argument_binding=ArgumentBinding.MERGE,
    )


def test_defaulted_param_is_exempt() -> None:
    """A param with a default has a graceful fallback — no source required."""

    def fn(*, user: Any, project_id: str, region: str = "us-east") -> None: ...  # noqa: ARG001

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=_OneField,
        callable_=fn,
        argument_binding=ArgumentBinding.MERGE,
    )


def test_spec_kwargs_provides_opts_in_to_provider_source() -> None:
    """Listing a param in ``spec_kwargs_provides`` acknowledges
    ``spec.kwargs(...)`` will supply it.
    """

    def fn(*, user: Any, tenant_id: int) -> None: ...  # noqa: ARG001

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=None,  # no serializer needed when provider supplies
        callable_=fn,
        argument_binding=ArgumentBinding.MERGE,
        spec_kwargs_provides=frozenset({"tenant_id"}),
    )


def test_data_only_does_not_count_serializer_fields_as_sources() -> None:
    """In DATA_ONLY, the validated payload is bundled into ``data``;
    individual field names never reach the callable as kwargs. A
    callable that declares ``data`` (so the DATA_ONLY shape check
    passes) but *also* declares an additional required param like
    ``tenant_id`` is still flagged — that name isn't a pool seed and
    DATA_ONLY doesn't spread serializer fields.
    """

    def fn(*, data: Any, tenant_id: int) -> None: ...  # noqa: ARG001

    with pytest.raises(ImproperlyConfigured, match="tenant_id"):
        validate_input_serializer_against_callable(
            label="x",
            input_serializer=_TwoField,
            callable_=fn,
            argument_binding=ArgumentBinding.DATA_ONLY,
        )


def test_trust_mode_skips_required_param_check_for_non_seed_params() -> None:
    """``input_serializer=None`` + MERGE = "client args spread verbatim";
    required params are presumed satisfied by the raw arguments dict.
    """

    def fn(*, user: Any, number: str, amount_cents: int) -> None: ...  # noqa: ARG001

    validate_input_serializer_against_callable(
        label="x",
        input_serializer=None,
        callable_=fn,
        argument_binding=ArgumentBinding.MERGE,
    )


def test_register_selector_tool_threads_spec_kwargs_provides() -> None:
    """Without the opt-in this would raise; with it, registration succeeds."""
    from rest_framework_mcp.server.mcp_server import MCPServer

    server = MCPServer(name="t")

    def selector(*, user: Any, project_id: str, tenant_id: int) -> list[Any]:  # noqa: ARG001
        return []

    # ``project_id`` is in the serializer; ``tenant_id`` is meant to
    # come from ``spec.kwargs(...)`` — without the opt-in the check
    # has no static source for it and raises.
    with pytest.raises(ImproperlyConfigured, match="tenant_id"):
        server.register_selector_tool(
            name="x",
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
            input_serializer=_OneField,
        )

    binding = server.register_selector_tool(
        name="y",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
        input_serializer=_OneField,
        spec_kwargs_provides=("tenant_id",),
    )
    assert binding.name == "y"
