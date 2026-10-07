"""``build_service_tool_input_schema`` for a serializer-less service spread into its parameters.

With no ``input_serializer``, drf-services' dispatch under a ``SPREAD_*`` binding
takes the caller's input as the service's own parameters and declares them, so
``UnknownArguments.REJECT`` admits them. The schema lists them, reflected by
drf-services' ``spec_to_json_schema`` given the binding, less every name the
server fills. Under ``BUNDLE`` nothing reads them, so none is listed.

Bindings are built directly rather than through the adapter, so each test reads
the schema a binding advertises, whatever registration would have said about it.
The last test registers one and calls it on every route, because what the schema
should offer is decided by which value a call is served.
"""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework_services import DEFAULT_POOL_SEEDS, UnsetType
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import ArgumentBinding, UnknownArguments
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
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
    ("provider", "argument_binding", "properties", "required"),
    [
        # Under ``SPREAD_AUTHOR_WINS`` the provider is applied over the caller's
        # spread, so a key it says it fills always reaches the service as the
        # provider's: offering it would ask for a value the call then replaces.
        pytest.param(
            _typed_provider,
            ArgumentBinding.SPREAD_AUTHOR_WINS,
            {"ticket", "resolution", "note"},
            {"ticket", "resolution"},
            id="typed-author-wins",
        ),
        # Under ``SPREAD_CALLER_WINS`` the caller's spread is applied last, so
        # the caller's value is the one served, and the name is offered.
        pytest.param(
            _typed_provider,
            ArgumentBinding.SPREAD_CALLER_WINS,
            {"ticket", "resolution", "tenant", "note"},
            {"ticket", "resolution"},
            id="typed-caller-wins",
        ),
        # One that may decline ``tenant`` with ``UNSET`` leaves the caller to
        # send it or not, so it is offered and not required under either.
        *(
            pytest.param(
                _declining_provider,
                binding,
                {"ticket", "resolution", "tenant", "note"},
                {"ticket", "resolution"},
                id=f"declinable-{binding.name}",
            )
            for binding in _SPREAD
        ),
        # An untyped one may fill any name and fills none for certain, so every
        # parameter is offered and none is required for lacking a default.
        *(
            pytest.param(
                lambda view, request: {"tenant": 1},
                binding,
                {"ticket", "resolution", "tenant", "note"},
                set(),
                id=f"untyped-{binding.name}",
            )
            for binding in _SPREAD
        ),
    ],
)
def test_a_name_the_services_provider_fills_is_offered_where_the_callers_value_is_served(
    provider: Any, argument_binding: ArgumentBinding, properties: set[str], required: set[str]
) -> None:
    # Not required wherever the provider may fill it, because the provider
    # fills it when the caller sends none.
    spec = ServiceSpec(service=_close_ticket, atomic=False, kwargs=provider)

    schema = build_service_tool_input_schema(_binding(spec, argument_binding))

    assert set(schema["properties"]) == properties
    assert set(schema.get("required", [])) == required


class _Reason(TypedDict):
    reason: str


def _reason_of_record(view: Any) -> _Reason:
    return {"reason": "provider"}


def _archive_with_reason(*, reason: str) -> dict[str, Any]:
    return {"reason": reason}


async def _served(server: MCPServer, route: str, arguments: dict[str, Any]) -> Any:
    """What calling ``archive`` through ``route`` returns as structured content."""
    if route == "call_tool":
        out: Any = (
            await sync_to_async(server.call_tool)("archive", arguments, user=None)
        ).to_dict()
    elif route == "acall_tool":
        out = await server.acall_tool("archive", arguments, user=None)
    else:
        context = MCPCallContext(
            http_request=HttpRequest(),
            token=TokenInfo(user=None),
            tools=server.tools,
            resources=server.resources,
            prompts=server.prompts,
            protocol_version="2025-11-25",
        )
        params = {"name": "archive", "arguments": arguments}
        out = await (
            handle_tools_call_async(params, context)
            if route == "async_handler"
            else sync_to_async(handle_tools_call)(params, context)
        )
    assert isinstance(out, dict)
    assert out.get("isError") is not True, out
    return out["structuredContent"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["handler", "async_handler", "call_tool", "acall_tool"])
@pytest.mark.parametrize(
    ("binding", "served", "advertised"),
    [
        # The provider is applied over the spread, so the caller's value is
        # replaced and the name is not offered; ``REJECT`` still admits it.
        (ArgumentBinding.SPREAD_AUTHOR_WINS, "provider", False),
        (ArgumentBinding.SPREAD_CALLER_WINS, "caller", True),
    ],
)
async def test_a_spread_services_provider_filled_name_is_offered_where_the_callers_value_is_served(
    binding: ArgumentBinding, served: str, advertised: bool, route: str
) -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="archive",
        spec=ServiceSpec(service=_archive_with_reason, atomic=False, kwargs=_reason_of_record),
        argument_binding=binding,
        unknown_arguments=UnknownArguments.REJECT,
    )
    listed: Any = server.list_tools(user=None)

    out = await _served(server, route, {"reason": "caller"})

    assert ("reason" in listed["tools"][0]["inputSchema"].get("properties", {})) is advertised
    assert out == {"reason": served}
