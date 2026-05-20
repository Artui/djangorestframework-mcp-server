"""End-to-end unknown-argument policy behaviour through the live transport.

Verifies the three policies' wire shape:

- ``REJECT`` → ``-32602`` + ``non_field_errors`` detail.
- ``PASSTHROUGH`` → unknown key reaches the selector via ``**rest``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.urls("tests.conformance.urls")


@pytest.mark.django_db(transaction=True)
def test_reject_unknown_key_returns_minus_32602(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {
            "name": "conformance.reject_unknown",
            "arguments": {"project_id": "p1", "rogue": "x"},
        },
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == -32602
    detail = body["error"]["data"]["detail"]
    assert "non_field_errors" in detail
    assert "rogue" in detail["non_field_errors"][0]


@pytest.mark.django_db(transaction=True)
def test_reject_unknown_accepts_known_only(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {"name": "conformance.reject_unknown", "arguments": {"project_id": "p1"}},
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert "result" in body, body


@pytest.mark.django_db(transaction=True)
def test_passthrough_carries_unknown_keys_into_callable(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {
            "name": "conformance.passthrough_unknown",
            "arguments": {"project_id": "p1", "since": "2026-01-01"},
        },
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    structured = response.json()["result"]["structuredContent"]
    # Selector returns ``[{"project_id": ..., "rest": kwargs}]``. The
    # ``since`` extra survives PASSTHROUGH merge and lands in ``rest``.
    assert structured[0]["project_id"] == "p1"
    assert structured[0]["rest"]["since"] == "2026-01-01"
