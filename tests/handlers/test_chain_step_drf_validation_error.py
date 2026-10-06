"""A chain step raising DRF's ``ValidationError`` is a ``validation_error`` result.

A service runs ``serializer.is_valid(raise_exception=True)`` as a matter of
course, and a selector step can refuse a value the same way. The step arm caught
drf-services' ``ServiceValidationError`` only, so DRF's exception escaped the
chain dispatcher: ``acall_tool`` and the sync handler raised it, and the wire
answered HTTP 500 / ``-32603``. The same exception from a service tool is an
``isError`` result, so a chain step now answers as a service tool does, with
``failedStep`` beside it, and an atomic chain still unwinds the steps before it.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from tests.testapp.models import Invoice
from tests.utils import tool_error

_ROUTES = ["handler", "async_handler", "acall_tool"]


def _create(*, number: str, amount_cents: int) -> Invoice:
    return Invoice.objects.create(number=number, amount_cents=amount_cents)


def _refuse(**_: Any) -> None:
    # What ``serializer.is_valid(raise_exception=True)`` raises inside a service.
    raise drf_serializers.ValidationError({"number": ["An invoice with this number exists."]})


def _refusing_step(kind: str) -> ChainStep:
    """The step that refuses, as a service step or as a selector step."""
    if kind == "service":
        return ChainStep("second", ServiceSpec(service=_refuse, atomic=False))
    return ChainStep("second", SelectorSpec(kind=SelectorKind.LIST, selector=_refuse))


def _server(kind: str, *, atomic: bool = True) -> MCPServer:
    """A chain whose first step writes a row and whose second step refuses."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_chain_tool(
        name="chain",
        description="Record an invoice.",
        atomic=atomic,
        steps=[
            ChainStep(
                "first",
                ServiceSpec(service=_create, atomic=False),
                inputs=lambda ctx: {"number": "INV-1", "amount_cents": 1},
            ),
            _refusing_step(kind),
        ],
    )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        conventions=server.conventions,
    )


async def _via(server: MCPServer, route: str) -> Any:
    """The result of calling the chain through ``route``."""
    if route == "acall_tool":
        return await server.acall_tool("chain", {}, user=None)
    params: dict[str, Any] = {"name": "chain", "arguments": {}}
    if route == "async_handler":
        return await handle_tools_call_async(params, _ctx(server))
    # Off the event loop, where the sync handler's ORM work is allowed.
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["service", "selector"])
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_steps_drf_validation_error_is_a_validation_error_result(
    route: str, kind: str
) -> None:
    out = await _via(_server(kind), route)

    assert tool_error(out) == {
        "type": "validation_error",
        # DRF's error has no message of its own, so it keeps the one a service
        # tool answers the same exception with.
        "message": "Invalid arguments",
        "failedStep": "second",
        "detail": {"number": ["An invoice with this number exists."]},
    }
    # The first step's write unwound with the chain's transaction.
    assert await sync_to_async(Invoice.objects.count)() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_non_atomic_chain_keeps_the_steps_before_a_drf_refusal(route: str) -> None:
    # The other half of the rollback assertion above: without the chain's
    # transaction the first write stands, so the count of zero there is the
    # rollback and not a step that never wrote.
    out = await _via(_server("service", atomic=False), route)

    assert tool_error(out)["failedStep"] == "second"
    assert await sync_to_async(Invoice.objects.count)() == 1


@pytest.mark.django_db(transaction=True)
async def test_a_steps_bare_drf_validation_error_keeps_its_list_detail() -> None:
    # ``ValidationError("...")`` carries a list rather than a field mapping; the
    # detail is DRF's as raised, whichever shape it has.
    def _refuse_plainly(**_: Any) -> None:
        raise drf_serializers.ValidationError("Not today.")

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_chain_tool(
        name="chain",
        description="Record an invoice.",
        steps=[ChainStep("only", ServiceSpec(service=_refuse_plainly))],
    )

    error = tool_error(await server.acall_tool("chain", {}, user=None))

    assert error["failedStep"] == "only"
    assert error["detail"] == ["Not today."]


@pytest.mark.django_db(transaction=True)
async def test_a_steps_service_validation_error_keeps_its_own_message() -> None:
    # The arm picks the message by the exception's type: only DRF's, which has
    # none, gets the generic one. A kernel refusal's message is the service's.
    def _refuse_in_the_kernel(**_: Any) -> None:
        raise ServiceValidationError("That number is taken.")

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_chain_tool(
        name="chain",
        description="Record an invoice.",
        steps=[ChainStep("only", ServiceSpec(service=_refuse_in_the_kernel))],
    )

    error = tool_error(await server.acall_tool("chain", {}, user=None))

    assert error["message"] == "That number is taken."
    assert error["failedStep"] == "only"
