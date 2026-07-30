"""End-to-end coverage: spec.permission_classes through HTTP.

Unit tests pin the adapter wrap; this file proves the wrapped
permission denies a real client request through the full transport.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.urls("tests.conformance.urls")


@pytest.mark.django_db(transaction=True)
def test_spec_permission_classes_deny_anonymous_user(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {"name": "conformance.gated", "arguments": {}},
        session_id=initialized_session,
    )
    # ``IsAuthenticated`` wraps via ``DRFPermissionAdapter`` and denies
    # the AllowAnyBackend-issued anonymous token. The transport surfaces that
    # as HTTP 403 — normative in the MCP authorization spec's error table —
    # with JSON-RPC ``-32002`` (FORBIDDEN) in the body.
    assert response.status_code == 403, response.content
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == -32002
    # A non-scope permission: 403, but no scope to advertise, so no
    # ``insufficient_scope`` / ``scope=`` in the challenge.
    assert "scope=" not in response["WWW-Authenticate"]
