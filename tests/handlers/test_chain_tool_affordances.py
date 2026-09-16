"""A chain step honours the ``affordances`` of the service it runs.

A chain dispatches each step's service directly rather than through
``dispatch_spec``, so every gate the core would apply has to be run here as well.
Permissions and preconditions always were; the service's own affordances were not,
so a call refused as a tool of its own ran -- and succeeded -- as a chain step. These
tests hold the three things that matter: the service does not run, the refusal is
the declaration's, and it answers in the core's order, after the target is judged
and before the step's preconditions.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db.models import Q
from django.http import HttpRequest
from rest_framework_services.exceptions.service_conflict import ServiceConflict
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from tests.testapp.models import Invoice
from tests.utils import tool_error

_CLOSED = Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)
_UNSENT = Affordance(
    code="invoice_sent", reason="A sent invoice cannot be voided.", when=Q(sent=False)
)


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend())


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _refusable_chain(server: MCPServer, ran: list[str]) -> None:
    def cancel(**_: Any) -> dict[str, str]:
        ran.append("cancel")
        return {"status": "cancelled"}

    server.register_chain_tool(
        name="chain",
        atomic=False,
        steps=[
            ChainStep("cancel", ServiceSpec(service=cancel, atomic=False, affordances=[_CLOSED]))
        ],
    )


def test_a_refused_step_does_not_run_and_names_the_step() -> None:
    server = _server()
    ran: list[str] = []
    _refusable_chain(server, ran)

    out = handle_tools_call({"name": "chain", "arguments": {}}, _ctx(server))

    error = tool_error(out)
    assert ran == []
    assert error["message"] == "The books are closed."
    assert error["failedStep"] == "cancel"


async def test_the_async_transport_refuses_the_same_step() -> None:
    server = _server()
    ran: list[str] = []
    _refusable_chain(server, ran)

    out: Any = await server.acall_tool("chain", user=None)

    error = tool_error(out)
    assert ran == []
    assert error["message"] == "The books are closed."


@pytest.mark.django_db
@pytest.mark.parametrize(("sent", "refused"), [(True, True), (False, False)])
def test_a_row_condition_judges_the_target_the_step_resolved(sent: bool, refused: bool) -> None:
    invoice = Invoice.objects.create(number="A-1", amount_cents=100, sent=sent)
    server = _server()
    ran: list[int] = []

    def void(*, instance: Invoice) -> dict[str, bool]:
        ran.append(instance.pk)
        return {"voided": True}

    server.register_chain_tool(
        name="chain",
        atomic=False,
        steps=[
            ChainStep(
                "void",
                ServiceSpec(service=void, atomic=False, affordances=[_UNSENT]),
                inputs=lambda ctx: {"instance": invoice},
            )
        ],
    )

    out: Any = handle_tools_call({"name": "chain", "arguments": {}}, _ctx(server))

    if refused:
        assert tool_error(out)["message"] == "A sent invoice cannot be voided."
        assert ran == []
    else:
        assert out.get("isError") is not True
        assert ran == [invoice.pk]


def test_the_affordance_answers_before_the_steps_preconditions() -> None:
    """The core's order: affordances, then preconditions.

    Both refuse here, so the message says which one answered -- and it must not be
    the precondition's.
    """

    def precondition(**_: Any) -> None:
        raise ServiceConflict("the precondition answered")

    server = _server()
    server.register_chain_tool(
        name="chain",
        atomic=False,
        steps=[
            ChainStep(
                "cancel",
                ServiceSpec(
                    service=lambda **_: None,
                    atomic=False,
                    affordances=[_CLOSED],
                    preconditions=[precondition],
                ),
            )
        ],
    )

    error = tool_error(handle_tools_call({"name": "chain", "arguments": {}}, _ctx(server)))

    assert error["message"] == "The books are closed."
    assert "precondition" not in error["message"]
