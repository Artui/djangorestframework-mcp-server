"""The three wire objects, in isolation from the flow that produces them."""

from __future__ import annotations

from typing import Any

import pytest

from rest_framework_mcp.constants import ELICITATION_KEY, ElicitAction
from rest_framework_mcp.elicitation.can_ask_client import can_ask_client
from rest_framework_mcp.elicitation.read_elicit_answer import read_elicit_answer
from rest_framework_mcp.elicitation.types.elicit_request import ElicitRequest
from rest_framework_mcp.elicitation.types.input_required_result import InputRequiredResult

SCHEMA: dict[str, Any] = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


# ---------- ElicitRequest ----------


def test_an_elicit_request_names_its_mode_explicitly() -> None:
    """``"form"`` is the schema default, but a client supporting both modes
    branches on the field, and saving one key is not worth making it know the
    rule."""
    assert ElicitRequest("Confirm?", SCHEMA).to_dict() == {
        "method": "elicitation/create",
        "params": {"mode": "form", "message": "Confirm?", "requestedSchema": SCHEMA},
    }


# ---------- InputRequiredResult ----------


def test_the_result_names_its_own_type() -> None:
    """Stamped here rather than by the response envelope, which defaults every
    result to ``complete`` and steps aside only for one that already said
    otherwise."""
    out = InputRequiredResult(
        input_requests={ELICITATION_KEY: ElicitRequest("Confirm?", SCHEMA)},
        request_state="token",
    ).to_dict()
    assert out["resultType"] == "input_required"
    assert out["inputRequests"][ELICITATION_KEY]["method"] == "elicitation/create"
    assert out["requestState"] == "token"


def test_state_alone_is_a_legal_result() -> None:
    """The spec's load-shedding case — *"come back with this and I will carry
    on"*, no question asked. Nothing here produces one, but the type permits
    what the spec permits."""
    assert InputRequiredResult(request_state="token").to_dict() == {
        "resultType": "input_required",
        "requestState": "token",
    }


def test_a_question_alone_is_a_legal_result() -> None:
    out = InputRequiredResult(
        input_requests={ELICITATION_KEY: ElicitRequest("Confirm?", SCHEMA)}
    ).to_dict()
    assert "requestState" not in out


def test_a_result_with_neither_is_refused() -> None:
    """*"At least one of ``inputRequests`` or ``requestState`` MUST be
    present"*. One with neither tells the client nothing it can act on, so it is
    a construction error rather than something to discover on the wire."""
    with pytest.raises(ValueError, match="must carry"):
        InputRequiredResult()


# ---------- reading the answer back ----------


def test_an_accepted_form_is_read() -> None:
    answer = read_elicit_answer(
        {"inputResponses": {ELICITATION_KEY: {"action": "accept", "content": {"ok": True}}}}
    )
    assert answer is not None
    assert answer.action is ElicitAction.ACCEPT
    assert answer.content == {"ok": True}


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_a_refusal_carries_no_content(action: str) -> None:
    answer = read_elicit_answer({"inputResponses": {ELICITATION_KEY: {"action": action}}})
    assert answer is not None
    assert answer.content == {}


def test_other_keys_are_ignored_rather_than_rejected() -> None:
    """The spec's instruction, and what keeps a client free to batch answers for
    requests this server never sent."""
    answer = read_elicit_answer(
        {
            "inputResponses": {
                "someone-elses": {"action": "accept"},
                ELICITATION_KEY: {"action": "accept", "content": {"ok": True}},
            }
        }
    )
    assert answer is not None and answer.content == {"ok": True}


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"inputResponses": None},
        {"inputResponses": []},
        {"inputResponses": {}},
        {"inputResponses": {ELICITATION_KEY: "accept"}},
        {"inputResponses": {ELICITATION_KEY: {}}},
        {"inputResponses": {ELICITATION_KEY: {"action": "maybe"}}},
    ],
)
def test_anything_unusable_reads_as_no_answer(params: dict[str, Any]) -> None:
    """⚠ Deliberately the same outcome as "the client did not answer": the
    service raises again, the question is re-issued, and the spec's *"respond
    with a new InputRequiredResult rather than returning an error"* happens
    without this layer knowing it is happening."""
    assert read_elicit_answer(params) is None


def test_content_that_is_not_an_object_is_treated_as_empty() -> None:
    answer = read_elicit_answer(
        {"inputResponses": {ELICITATION_KEY: {"action": "accept", "content": "yes"}}}
    )
    assert answer is not None and answer.content == {}


# ---------- the capability gate ----------


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        ({"elicitation": {}}, True),
        ({"elicitation": {"form": {}}}, True),
        ({"elicitation": {"form": {}, "url": {}}}, True),
        ({"elicitation": {"url": {}}}, False),
        ({}, False),
        ({"elicitation": None}, False),
        ({"elicitation": True}, False),
        ({"sampling": {}}, False),
    ],
)
def test_who_may_be_asked(capabilities: dict[str, Any], expected: bool) -> None:
    """⚠ ``{}`` means yes. Form was the only mode before this revision added URL
    mode, so a client that omits the sub-keys is declaring the original
    behaviour — which is exactly what the schema's "form mode only (implicit)"
    example shows. A *non-empty* object is an enumeration, so url-only is a
    client this package cannot ask."""
    assert can_ask_client(capabilities) is expected
