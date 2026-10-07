"""``build_service_tool_input_schema`` for a serializer-less service spread into its parameters.

With no ``input_serializer``, drf-services' dispatch under a ``SPREAD_*`` binding
takes the caller's input as the service's own parameters and declares them, so
``UnknownArguments.REJECT`` admits them. The schema lists them, reflected by
drf-services' ``spec_to_json_schema`` given the binding, less every name the
server fills. Under ``BUNDLE`` nothing reads them, so none is listed.

Bindings are built directly rather than through the adapter, so each test reads
the schema a binding advertises, whatever registration would have said about it.
"""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from rest_framework_services import DEFAULT_POOL_SEEDS, UnsetType
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import ArgumentBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.schema.service_tool_schema import build_service_tool_input_schema

_SPREAD = (ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS)


def _close_ticket(
    *, user: Any, ticket: int, resolution: str, tenant: int, note: str = ""
) -> dict[str, Any]:
    return {}


def _binding(spec: ServiceSpec, argument_binding: ArgumentBinding) -> ToolBinding:
    return ToolBinding(name="close", description=None, spec=spec, argument_binding=argument_binding)


@pytest.mark.parametrize("argument_binding", _SPREAD)
def test_a_spread_service_lists_its_own_parameters(argument_binding: ArgumentBinding) -> None:
    # ``user`` is a seed, so it is not asked of the client; every other
    # parameter without a default is required, because nothing but the caller
    # fills it.
    spec = ServiceSpec(service=_close_ticket, atomic=False)

    schema = build_service_tool_input_schema(_binding(spec, argument_binding))

    assert set(schema["properties"]) == {"ticket", "resolution", "tenant", "note"}
    assert set(schema["required"]) == {"ticket", "resolution", "tenant"}


def test_a_registered_seed_is_not_listed_as_a_service_parameter() -> None:
    # Dispatch subtracts the server's registered seeds from what it declares, and
    # strips the caller's value for one, so the schema neither lists nor
    # requires it.
    spec = ServiceSpec(service=_close_ticket, atomic=False)
    seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda: 1)

    schema = build_service_tool_input_schema(
        _binding(spec, ArgumentBinding.SPREAD_AUTHOR_WINS), pool_seeds=seeds
    )

    assert set(schema["properties"]) == {"ticket", "resolution", "note"}
    assert set(schema["required"]) == {"ticket", "resolution"}


class _TenantScope(TypedDict):
    tenant: int


def _typed_provider(view: Any, request: Any) -> _TenantScope:
    return {"tenant": 1}


class _MaybeTenantScope(TypedDict):
    tenant: int | UnsetType


def _declining_provider(view: Any, request: Any) -> _MaybeTenantScope:
    return {"tenant": 1}


@pytest.mark.parametrize(
    ("provider", "required"),
    [
        # A typed provider says it fills ``tenant``, and only ``tenant``.
        pytest.param(_typed_provider, {"ticket", "resolution"}, id="typed"),
        # One that may decline ``tenant`` with ``UNSET`` leaves the caller to
        # send it or not, so it is offered and not required either.
        pytest.param(_declining_provider, {"ticket", "resolution"}, id="declinable"),
        # An untyped one may fill any name, so none is required for lacking a
        # default.
        pytest.param(lambda view, request: {"tenant": 1}, set(), id="untyped"),
    ],
)
@pytest.mark.parametrize("argument_binding", _SPREAD)
def test_a_name_the_services_provider_fills_is_offered_but_not_required(
    provider: Any, required: set[str], argument_binding: ArgumentBinding
) -> None:
    # Dispatch admits a provider-filled name, so it is listed; it is not
    # required, because the provider fills it when the caller sends none.
    spec = ServiceSpec(service=_close_ticket, atomic=False, kwargs=provider)

    schema = build_service_tool_input_schema(_binding(spec, argument_binding))

    assert set(schema["properties"]) == {"ticket", "resolution", "tenant", "note"}
    assert set(schema.get("required", [])) == required
