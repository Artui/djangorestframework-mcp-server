"""The exchange over the actual HTTP transport.

The handler suite proves the logic; this proves a conformant client can drive it
— that ``inputResponses`` and ``requestState`` survive the transport, that the
result comes back inside a ``200`` rather than an error status, and that the
retry is what the spec says it is: **a second, independent request**, with its
own id, on a server holding nothing in between.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client

from rest_framework_mcp.constants import ELICITATION_KEY
from tests.elicitation.conftest import TOOL

MODERN = "2026-07-28"

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.urls("tests.elicitation.urls")]


def _post(
    client: Client,
    params: dict[str, Any],
    *,
    can_elicit: bool = True,
    request_id: int = 1,
) -> Any:
    body: dict[str, Any] = {
        **params,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientCapabilities": (
                {"elicitation": {}} if can_elicit else {}
            ),
        },
    }
    return client.post(
        "/mcp/",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": body}
        ),
        content_type="application/json",
        headers={
            "Mcp-Protocol-Version": MODERN,
            "Mcp-Method": "tools/call",
            "Mcp-Name": TOOL,
        },
    )


def _result(response: Any) -> dict[str, Any]:
    assert response.status_code == 200
    return json.loads(response.content)["result"]


def test_the_question_comes_back_inside_an_ordinary_success(client: Client) -> None:
    """⚠ ``200`` with a ``result``, not an error status and not an ``error``
    member. A client that reads the status alone must see nothing unusual."""
    result = _result(_post(client, {"name": TOOL, "arguments": {"count": 400}}))
    assert result["resultType"] == "input_required"
    assert result["inputRequests"][ELICITATION_KEY]["method"] == "elicitation/create"


def test_a_client_can_complete_the_round_trip(client: Client) -> None:
    asked = _result(_post(client, {"name": TOOL, "arguments": {"count": 400}}))
    done = _result(
        _post(
            client,
            {
                "name": TOOL,
                "arguments": {"count": 400},
                "inputResponses": {
                    ELICITATION_KEY: {"action": "accept", "content": {"confirmed": True}}
                },
                "requestState": asked["requestState"],
            },
            request_id=2,
        )
    )
    assert done["resultType"] == "complete"
    assert done["structuredContent"] == {"deleted": 400, "confirmed": True, "reason": ""}


def test_the_retry_is_an_independent_request(client: Client) -> None:
    """⭐ The whole reason MRTR replaced server-initiated requests. Different
    JSON-RPC id, no session header, nothing correlating the two but the token
    the client carried — which is what lets the second request land on a
    different process entirely."""
    asked = _result(
        _post(
            client,
            {
                "name": TOOL,
                "arguments": {"count": 400},
            },
        )
    )
    response = _post(
        client,
        {
            "name": TOOL,
            "arguments": {"count": 400},
            "inputResponses": {
                ELICITATION_KEY: {"action": "accept", "content": {"confirmed": True}}
            },
            "requestState": asked["requestState"],
        },
        request_id=99,
    )
    assert json.loads(response.content)["id"] == 99
    assert "Mcp-Session-Id" not in response


def test_a_client_that_cannot_be_asked_still_learns_what_is_missing(client: Client) -> None:
    result = _result(_post(client, {"name": TOOL, "arguments": {"count": 400}}, can_elicit=False))
    assert result["resultType"] == "complete"
    assert result["isError"] is True
    error = json.loads(result["content"][0]["text"])["error"]
    assert error["requestedInput"] == {"confirmed": {"type": "boolean"}}


def test_a_call_that_needs_nothing_is_unchanged(client: Client) -> None:
    result = _result(_post(client, {"name": TOOL, "arguments": {"count": 5}}))
    assert result["resultType"] == "complete"
    assert result["structuredContent"]["deleted"] == 5
