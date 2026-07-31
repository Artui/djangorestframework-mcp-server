"""The multi-round trip, end to end through ``handle_tools_call``.

The pattern under test is the one ``2026-07-28`` put in place of
server-initiated requests: a call that needs something answers with an
``InputRequiredResult`` and *ends*, and the client comes back with a fresh
request carrying the answer. Nothing is held between the two — which is what
these tests are really pinning, since every one of them builds a new context
rather than reusing one.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core import signing

from rest_framework_mcp import MCPServer
from rest_framework_mcp.constants import ELICITATION_KEY, REQUEST_STATE_SALT
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from tests.elicitation.conftest import TOOL, context
from tests.utils import tool_error


def _call(server: MCPServer, arguments: dict[str, Any], **extra: Any) -> Any:
    """One ``tools/call``, with a context built fresh for it."""
    ctx = context(server, **extra.pop("ctx", {}))
    return handle_tools_call({"name": TOOL, "arguments": arguments, **extra}, ctx)


def _accept(content: dict[str, Any]) -> dict[str, Any]:
    return {ELICITATION_KEY: {"action": "accept", "content": content}}


# ---------- the question ----------


def test_a_call_that_needs_nothing_is_untouched(server: MCPServer) -> None:
    """The overwhelming majority of calls. Nothing about the exchange should be
    visible to a tool that never asks."""
    out = _call(server, {"count": 5})
    assert out["structuredContent"] == {"deleted": 5, "confirmed": False, "reason": ""}
    assert "resultType" not in out


def test_a_service_that_asks_gets_an_input_required_result(server: MCPServer) -> None:
    out = _call(server, {"count": 400})
    assert out["resultType"] == "input_required"
    assert set(out) == {"resultType", "inputRequests", "requestState"}


def test_the_question_is_an_elicitation_request_in_the_specs_shape(server: MCPServer) -> None:
    request = _call(server, {"count": 400})["inputRequests"][ELICITATION_KEY]
    assert request == {
        "method": "elicitation/create",
        "params": {
            "mode": "form",
            "message": "400 rows match. Confirm to proceed.",
            "requestedSchema": {
                "type": "object",
                "properties": {"confirmed": {"type": "boolean"}},
                "required": ["confirmed"],
            },
        },
    }


def test_the_service_message_is_carried_verbatim(server: MCPServer) -> None:
    """The service composed a sentence about *its* data — 400 rows — and that
    sentence is the only thing the user will read."""
    message = _call(server, {"count": 987})["inputRequests"][ELICITATION_KEY]["params"]["message"]
    assert message == "987 rows match. Confirm to proceed."


def test_the_result_is_not_an_error(server: MCPServer) -> None:
    """⚠ Worth pinning: this is a *successful* result with a different shape,
    not a failure. A client that treats it as one would never retry."""
    out = _call(server, {"count": 400})
    assert "isError" not in out
    assert "content" not in out


# ---------- the answer ----------


def test_retrying_with_the_answer_completes_the_call(server: MCPServer) -> None:
    asked = _call(server, {"count": 400})
    out = _call(
        server,
        {"count": 400},
        inputResponses=_accept({"confirmed": True}),
        requestState=asked["requestState"],
    )
    assert out["structuredContent"] == {"deleted": 400, "confirmed": True, "reason": ""}


def test_the_answer_reaches_the_service_as_an_ordinary_argument(server: MCPServer) -> None:
    """⭐ The load-bearing claim of the whole design: the service declared
    ``confirmed`` as a normal serializer field and read it as one. It has no
    idea a dialog happened."""
    asked = _call(server, {"count": 400})
    out = _call(
        server,
        {"count": 400},
        inputResponses=_accept({"confirmed": True}),
        requestState=asked["requestState"],
    )
    assert out["structuredContent"]["confirmed"] is True


def test_an_answer_is_accepted_without_any_request_state(server: MCPServer) -> None:
    """A client that dropped the token is not an attacker — it is a client that
    will simply be asked again if the answer turns out not to be enough. There
    is nothing to defend here: anyone who can send ``inputResponses`` can send
    ``arguments``."""
    out = _call(server, {"count": 400}, inputResponses=_accept({"confirmed": True}))
    assert out["structuredContent"]["deleted"] == 400


def test_an_empty_form_submission_just_asks_again(server: MCPServer) -> None:
    """Accept with nothing in it. The service raises again, so the question is
    re-issued — the spec's own instruction for missing information."""
    asked = _call(server, {"count": 400})
    out = _call(
        server, {"count": 400}, inputResponses=_accept({}), requestState=asked["requestState"]
    )
    assert out["resultType"] == "input_required"


# ---------- more than one round ----------


def test_a_second_question_is_asked_after_the_first_is_answered(server: MCPServer) -> None:
    first = _call(server, {"count": 2000})
    second = _call(
        server,
        {"count": 2000},
        inputResponses=_accept({"confirmed": True}),
        requestState=first["requestState"],
    )
    assert second["resultType"] == "input_required"
    params = second["inputRequests"][ELICITATION_KEY]["params"]
    assert params["message"] == "Deleting more than 1000 rows needs a reason."
    assert params["requestedSchema"]["properties"] == {
        "reason": {"type": "string", "title": "Why?"}
    }


def test_the_first_answer_survives_the_second_question(server: MCPServer) -> None:
    """⭐ Why ``requestState`` carries the answers at all. The retry below sends
    only the *reason* — the client has no obligation to resend an earlier
    round's ``inputResponses`` — so without accumulation the service would ask
    for the confirmation a second time, forever."""
    first = _call(server, {"count": 2000})
    second = _call(
        server,
        {"count": 2000},
        inputResponses=_accept({"confirmed": True}),
        requestState=first["requestState"],
    )
    third = _call(
        server,
        {"count": 2000},
        inputResponses=_accept({"reason": "quarterly purge"}),
        requestState=second["requestState"],
    )
    assert third["structuredContent"] == {
        "deleted": 2000,
        "confirmed": True,
        "reason": "quarterly purge",
    }


def test_the_carried_answers_do_not_include_the_clients_own_arguments(server: MCPServer) -> None:
    """The state holds what the *user* supplied and nothing else. Folding the
    client's arguments in would mean a token that could rewrite the very call it
    is bound to."""
    asked = _call(server, {"count": 400})
    payload = signing.loads(asked["requestState"], salt=REQUEST_STATE_SALT)
    assert payload["a"] == {}


def test_the_state_grows_only_by_what_was_answered(server: MCPServer) -> None:
    first = _call(server, {"count": 2000})
    second = _call(
        server,
        {"count": 2000},
        inputResponses=_accept({"confirmed": True}),
        requestState=first["requestState"],
    )
    payload = signing.loads(second["requestState"], salt=REQUEST_STATE_SALT)
    assert payload["a"] == {"confirmed": True}
    assert payload["r"] == 2


# ---------- the user says no ----------


def test_a_declined_question_stops_the_call(server: MCPServer) -> None:
    asked = _call(server, {"count": 400})
    out = _call(
        server,
        {"count": 400},
        inputResponses={ELICITATION_KEY: {"action": "decline"}},
        requestState=asked["requestState"],
    )
    error = tool_error(out)
    assert error["type"] == "input_declined"
    assert error["action"] == "decline"


def test_a_cancelled_question_is_reported_as_its_own_thing(server: MCPServer) -> None:
    """⚠ Not the same as a decline, and the difference is worth a client's
    attention: nobody decided anything, so retrying is reasonable."""
    out = _call(server, {"count": 400}, inputResponses={ELICITATION_KEY: {"action": "cancel"}})
    error = tool_error(out)
    assert error["type"] == "input_cancelled"
    assert error["action"] == "cancel"


def test_a_refusal_does_not_run_the_service(server: MCPServer) -> None:
    """Nothing about a decline should reach the tool — including a decline on a
    call the tool would have run happily."""
    out = _call(server, {"count": 5}, inputResponses={ELICITATION_KEY: {"action": "decline"}})
    assert tool_error(out)["type"] == "input_declined"


def test_a_refusal_is_not_re_asked(server: MCPServer) -> None:
    out = _call(server, {"count": 400}, inputResponses={ELICITATION_KEY: {"action": "decline"}})
    assert "inputRequests" not in out


# ---------- clients that cannot be asked ----------


def test_a_client_that_declared_no_elicitation_is_told_what_is_missing(server: MCPServer) -> None:
    """⭐ Degrades rather than failing. The spec forbids *asking* such a client;
    it does not require a protocol error, and an error would throw away the one
    thing a model could act on."""
    out = _call(server, {"count": 400}, ctx={"capabilities": {}})
    error = tool_error(out)
    assert error["type"] == "input_required"
    assert error["message"] == "400 rows match. Confirm to proceed."
    assert error["requestedInput"] == {"confirmed": {"type": "boolean"}}


def test_a_url_only_client_cannot_be_sent_a_form(server: MCPServer) -> None:
    out = _call(server, {"count": 400}, ctx={"capabilities": {"elicitation": {"url": {}}}})
    assert tool_error(out)["type"] == "input_required"


def test_a_legacy_client_degrades_without_an_era_branch(server: MCPServer) -> None:
    """A legacy request declares its capabilities once, at ``initialize``, so
    this map is empty for it — and the capability gate answers correctly without
    anything anywhere testing the protocol version."""
    out = _call(server, {"count": 400}, ctx={"capabilities": {}, "protocol_version": "2025-11-25"})
    assert tool_error(out)["type"] == "input_required"


def test_a_service_that_cannot_say_what_it_needs_degrades(server: MCPServer) -> None:
    """``AdditionalInputRequired`` allows a bare message. There is no form to
    render from one, so the message is the whole answer."""
    ctx = context(server)
    out = handle_tools_call({"name": "rows.vague", "arguments": {"count": 1}}, ctx)
    error = tool_error(out)
    assert error["type"] == "input_required"
    assert error["message"] == "This needs something I cannot describe."
    assert "requestedInput" not in error


def test_an_unrenderable_schema_is_an_internal_error(server: MCPServer) -> None:
    """⚠ Loud on purpose, and checked *before* the capability gate — a service
    that asks for a nested object is broken for every caller, and finding out
    only when an elicitation-capable client happens to call is how it survives
    to production."""
    ctx = context(server)
    out = handle_tools_call({"name": "rows.unrenderable", "arguments": {"count": 1}}, ctx)
    assert isinstance(out, JsonRpcError)
    assert out.code == -32603
    assert "'profile'" in out.message


def test_an_unrenderable_schema_fails_even_for_a_client_that_cannot_be_asked(
    server: MCPServer,
) -> None:
    ctx = context(server, capabilities={})
    out = handle_tools_call({"name": "rows.unrenderable", "arguments": {"count": 1}}, ctx)
    assert isinstance(out, JsonRpcError)


# ---------- the round budget ----------


def test_a_service_that_can_never_be_satisfied_stops_asking(server: MCPServer) -> None:
    """Two questions with a budget of one: the second is refused rather than
    put to the user."""
    first = _call(server, {"count": 2000}, ctx={"max_input_rounds": 1})
    out = _call(
        server,
        {"count": 2000},
        inputResponses=_accept({"confirmed": True}),
        requestState=first["requestState"],
        ctx={"max_input_rounds": 1},
    )
    error = tool_error(out)
    assert error["type"] == "input_required"
    assert error["message"] == "Deleting more than 1000 rows needs a reason."


def test_the_budget_counts_rounds_not_calls(server: MCPServer) -> None:
    """A client that keeps dropping the token is never over budget — it is
    starting over each time, and the user is being asked once each time."""
    for _ in range(4):
        out = _call(server, {"count": 400}, ctx={"max_input_rounds": 1})
        assert out["resultType"] == "input_required"


# ---------- other registration shapes ----------


@pytest.mark.django_db
def test_a_chain_tool_degrades_instead_of_asking(server: MCPServer) -> None:
    """⛔ Deliberate. MRTR finishes a call by re-running it from the top, and a
    chain that asked at step three would re-run steps one and two on the retry.
    The subclassing does the right thing for free: the existing ``ServiceError``
    arm reports it as an ordinary failure."""
    from rest_framework_services.types.service_spec import ServiceSpec

    from rest_framework_mcp.registry.types.chain_step import ChainStep
    from tests.elicitation.conftest import DeleteInput, delete_rows

    server.register_chain_tool(
        name="rows.delete_chain",
        description="x",
        steps=[
            ChainStep(
                alias="wipe",
                spec=ServiceSpec(service=delete_rows, input_serializer=DeleteInput, atomic=False),
            )
        ],
    )
    out = handle_tools_call(
        {"name": "rows.delete_chain", "arguments": {"count": 400}}, context(server)
    )
    assert tool_error(out)["type"] == "service_error"


# ---------- async parity ----------


@pytest.mark.asyncio
async def test_the_async_path_asks_the_same_question(server: MCPServer) -> None:
    out = await handle_tools_call_async(
        {"name": TOOL, "arguments": {"count": 400}}, context(server)
    )
    assert out["resultType"] == "input_required"
    assert out["inputRequests"][ELICITATION_KEY]["params"]["requestedSchema"]["required"] == [
        "confirmed"
    ]


@pytest.mark.asyncio
async def test_the_async_path_accepts_the_answer(server: MCPServer) -> None:
    asked = await handle_tools_call_async(
        {"name": TOOL, "arguments": {"count": 400}}, context(server)
    )
    out = await handle_tools_call_async(
        {
            "name": TOOL,
            "arguments": {"count": 400},
            "inputResponses": _accept({"confirmed": True}),
            "requestState": asked["requestState"],
        },
        context(server),
    )
    assert out["structuredContent"]["deleted"] == 400


@pytest.mark.asyncio
async def test_the_async_path_honours_a_refusal(server: MCPServer) -> None:
    out = await handle_tools_call_async(
        {
            "name": TOOL,
            "arguments": {"count": 400},
            "inputResponses": {ELICITATION_KEY: {"action": "cancel"}},
        },
        context(server),
    )
    assert tool_error(out)["type"] == "input_cancelled"
