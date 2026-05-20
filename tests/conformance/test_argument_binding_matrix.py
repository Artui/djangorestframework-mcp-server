"""End-to-end argument-binding behaviour through the live HTTP transport.

Mirrors what an MCP client sees on the wire: a ``MERGE``-bound service
that declares its parameters as kwargs receives them spread from the
``arguments`` payload instead of as a ``data=`` dict. The unit tests
in ``tests/handlers/`` pin the pool-construction logic; this file pins
the wire shape after going through Django's URL conf + the JSON-RPC
transport + the session lifecycle.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.urls("tests.conformance.urls")


@pytest.mark.django_db(transaction=True)
def test_merge_binding_spreads_arguments_to_service_kwargs(
    jsonrpc, initialized_session: str
) -> None:
    response = jsonrpc(
        "tools/call",
        {
            "name": "conformance.merge",
            "arguments": {"number": "INV-1", "amount_cents": 100},
        },
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    structured = response.json()["result"]["structuredContent"]
    assert structured == {"number": "INV-1", "amount_cents": 100}


@pytest.mark.django_db(transaction=True)
def test_merge_binding_strips_reserved_pool_seeds_from_spread(
    jsonrpc, initialized_session: str
) -> None:
    """A client-supplied ``user`` key never reaches the callable's ``user`` arg."""
    response = jsonrpc(
        "tools/call",
        {
            "name": "conformance.merge",
            "arguments": {
                "number": "INV-2",
                "amount_cents": 50,
                "user": "evil-spoof",
            },
        },
        session_id=initialized_session,
    )
    # The service's signature has no ``user`` param, so the spread strip
    # is invisible to the service body. The proof is that the call
    # succeeds without ``unknown_arguments=REJECT`` flagging ``user`` —
    # reserved seeds are never considered "unknown".
    assert response.status_code == 200, response.content
