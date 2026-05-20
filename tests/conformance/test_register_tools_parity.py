"""End-to-end ``register_tools`` parity through the live transport.

Bulk-registered tools must show up in ``tools/list`` with the same
schema shape an imperative call would have produced. The unit suite
already pins field-for-field equivalence on the binding object; this
file proves the wire shape lands too.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.urls("tests.conformance.urls")


@pytest.mark.django_db(transaction=True)
def test_bulk_registered_tools_visible_in_tools_list(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc("tools/list", {}, session_id=initialized_session)
    assert response.status_code == 200, response.content
    names = {t["name"] for t in response.json()["result"]["tools"]}
    assert "conformance.bulk_listed" in names
    assert "conformance.bulk_gated" in names


@pytest.mark.django_db(transaction=True)
def test_bulk_registered_selector_callable(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {"name": "conformance.bulk_listed", "arguments": {}},
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    structured = response.json()["result"]["structuredContent"]
    assert structured == [{"sentinel": "bulk"}]


@pytest.mark.django_db(transaction=True)
def test_bulk_registered_service_inherits_permission_classes(
    jsonrpc, initialized_session: str
) -> None:
    """``spec.permission_classes`` flows through ``register_tools`` to the binding."""
    response = jsonrpc(
        "tools/call",
        {"name": "conformance.bulk_gated", "arguments": {}},
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    # ``IsAuthenticated`` denies the AllowAnyBackend-issued anonymous token.
    assert "error" in body
    assert body["error"]["code"] == -32002  # FORBIDDEN
