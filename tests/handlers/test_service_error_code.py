"""A refusal's ``code`` reaches the caller, on every path a ``ServiceError`` can take.

drf-services raises ``ActionUnavailable(reason, code=...)`` when a declared
affordance refuses a call, and asks a transport serving an agent to pass on both:
the reason is a sentence that gets reworded, the code is the name a client switches
on. Every ``ServiceError`` arm here built its result from the message alone, so the
code was dropped on all six of them.

One test per arm rather than one parametrised over a shared harness, so each site
is named where a reader -- and the coverage gate -- can see it held. Each asserts the
whole served error object, because the contract is as much the key that is *not*
there for a plain conflict as the one that is for a refusal.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework import serializers
from rest_framework_services.exceptions.action_unavailable import ActionUnavailable
from rest_framework_services.exceptions.service_conflict import ServiceConflict
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from tests.testapp.affordances import fresh_server
from tests.utils import tool_error

_REFUSED: dict[str, Any] = {
    "type": "service_error",
    "message": "The books are closed.",
    "code": "books_closed",
}
"""What a refusal serves: the pre-existing ``type`` and ``message``, and the code."""

_CONFLICT: dict[str, Any] = {"type": "service_error", "message": "That slot is taken."}
"""What a plain ``ServiceConflict`` serves: no ``code`` key at all, not a null one."""


class _RowSerializer(serializers.Serializer):
    number = serializers.CharField()


def _refused_service() -> ServiceSpec[Any, Any, Any]:
    """Refused by a declared affordance, the way a real refusal is raised."""
    return ServiceSpec(
        service=lambda **_: {"status": "ran"},
        atomic=False,
        affordances=[
            Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)
        ],
    )


def _conflicting_service() -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=lambda **_: {"status": "ran"}, atomic=False, preconditions=[_raise_conflict]
    )


def _raise_refusal(**_: Any) -> None:
    # A selector spec has no refusing affordances of its own, so a precondition
    # raising the refusal by hand is the only way one reaches a selector tool.
    raise ActionUnavailable("The books are closed.", code="books_closed")


def _raise_conflict(**_: Any) -> None:
    raise ServiceConflict("That slot is taken.")


def _selector(precondition: Any) -> SelectorSpec[Any, Any]:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda **_: {"number": "A-1"},
        output_serializer=_RowSerializer,
        preconditions=[precondition],
    )


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _call(server: MCPServer, name: str) -> Any:
    return handle_tools_call({"name": name, "arguments": {}}, _ctx(server))


# ---------- the service tool, sync transport (handle_tools_call) ----------


def test_a_refused_service_tool_serves_the_code() -> None:
    server = fresh_server()
    server.register_service_tool(name="svc", spec=_refused_service(), permissions=[])

    assert tool_error(_call(server, "svc")) == _REFUSED


def test_a_conflicting_service_tool_serves_no_code_key() -> None:
    server = fresh_server()
    server.register_service_tool(name="svc", spec=_conflicting_service(), permissions=[])

    assert tool_error(_call(server, "svc")) == _CONFLICT


# ---------- the service tool, async transport (handle_tools_call_async) ----------


async def test_a_refused_service_tool_serves_the_code_over_the_async_transport() -> None:
    server = fresh_server()
    server.register_service_tool(name="svc", spec=_refused_service(), permissions=[])

    assert tool_error(await server.acall_tool("svc", user=None)) == _REFUSED


async def test_a_conflicting_service_tool_serves_no_code_key_over_the_async_transport() -> None:
    server = fresh_server()
    server.register_service_tool(name="svc", spec=_conflicting_service(), permissions=[])

    assert tool_error(await server.acall_tool("svc", user=None)) == _CONFLICT


# ---------- the in-process core (call_spec_tool) ----------


def test_a_refused_service_tool_serves_the_code_through_call_tool() -> None:
    server = fresh_server()
    server.register_service_tool(name="svc", spec=_refused_service(), permissions=[])

    assert tool_error(server.call_tool("svc", user=None).to_dict()) == _REFUSED


def test_a_conflicting_service_tool_serves_no_code_key_through_call_tool() -> None:
    server = fresh_server()
    server.register_service_tool(name="svc", spec=_conflicting_service(), permissions=[])

    assert tool_error(server.call_tool("svc", user=None).to_dict()) == _CONFLICT


# ---------- the selector tool, both siblings (selector_tool_dispatch) ----------


def test_a_refused_selector_tool_serves_the_code() -> None:
    server = fresh_server()
    server.register_selector_tool(name="sel", spec=_selector(_raise_refusal), permissions=[])

    assert tool_error(_call(server, "sel")) == _REFUSED


def test_a_conflicting_selector_tool_serves_no_code_key() -> None:
    server = fresh_server()
    server.register_selector_tool(name="sel", spec=_selector(_raise_conflict), permissions=[])

    assert tool_error(_call(server, "sel")) == _CONFLICT


async def test_a_refused_selector_tool_serves_the_code_over_the_async_transport() -> None:
    server = fresh_server()
    server.register_selector_tool(name="sel", spec=_selector(_raise_refusal), permissions=[])

    assert tool_error(await server.acall_tool("sel", user=None)) == _REFUSED


async def test_a_conflicting_selector_tool_serves_no_code_key_over_the_async_transport() -> None:
    server = fresh_server()
    server.register_selector_tool(name="sel", spec=_selector(_raise_conflict), permissions=[])

    assert tool_error(await server.acall_tool("sel", user=None)) == _CONFLICT


# ---------- a chain step (chain_tool_dispatch) ----------


def test_a_refused_chain_step_serves_the_code_beside_the_failed_step() -> None:
    server = fresh_server()
    server.register_chain_tool(
        name="chain", atomic=False, steps=[ChainStep("void", _refused_service())], permissions=[]
    )

    assert tool_error(_call(server, "chain")) == {**_REFUSED, "failedStep": "void"}


def test_a_conflicting_chain_step_serves_no_code_key() -> None:
    server = fresh_server()
    server.register_chain_tool(
        name="chain",
        atomic=False,
        steps=[ChainStep("void", _conflicting_service())],
        permissions=[],
    )

    assert tool_error(_call(server, "chain")) == {**_CONFLICT, "failedStep": "void"}
