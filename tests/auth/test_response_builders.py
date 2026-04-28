from __future__ import annotations

import json

from rest_framework_mcp.auth.insufficient_scope_response import (
    build_insufficient_scope_response,
)
from rest_framework_mcp.auth.unauthenticated_response import build_unauthenticated_response


def test_build_unauthenticated_response_carries_challenge() -> None:
    resp = build_unauthenticated_response('Bearer realm="x"')
    assert resp.status_code == 401
    assert resp["WWW-Authenticate"] == 'Bearer realm="x"'
    body = json.loads(resp.content)
    assert body["error"] == "unauthorized"


def test_build_insufficient_scope_response_carries_challenge() -> None:
    resp = build_insufficient_scope_response('Bearer scope="x"')
    assert resp.status_code == 403
    assert resp["WWW-Authenticate"] == 'Bearer scope="x"'
    body = json.loads(resp.content)
    assert body["error"] == "insufficient_scope"
