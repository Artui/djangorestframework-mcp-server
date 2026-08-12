"""The JSON-RPC error codes the MCP spec names, pinned on the wire.

These are the only error codes the spec dictates for a server of this
shape, and each one has a client behaviour attached to it — a client may
special-case ``-32002`` to mean "that resource is gone" and ``-32602`` to
mean "the request itself was wrong, don't retry it unchanged". Asserting
them through the live transport (rather than on a handler's return value)
is deliberate: the number a client sees is the contract, and it survives
the envelope, the viewset and the renderer.

Integer literals, not :class:`JsonRpcErrorCode` members, for the same
reason — an assertion against the enum would happily follow the enum if
somebody renumbered it.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.urls("tests.conformance.urls")


@pytest.mark.django_db(transaction=True)
def test_unknown_resource_is_32002(jsonrpc, initialized_session: str) -> None:
    """``-32002`` is the one legacy code the 2026-07-28 revision keeps alive."""
    response = jsonrpc(
        "resources/read",
        {"uri": "conformance://nothing-here"},
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    error = response.json()["error"]
    assert error["code"] == -32002
    # The spec's worked example echoes the URI in ``data``.
    assert error["data"] == {"uri": "conformance://nothing-here"}


@pytest.mark.django_db(transaction=True)
def test_unknown_tool_is_32602(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {"name": "conformance.no_such_tool", "arguments": {}},
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    assert response.json()["error"]["code"] == -32602


@pytest.mark.django_db(transaction=True)
def test_unknown_prompt_is_32602(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "prompts/get",
        {"name": "conformance.no_such_prompt", "arguments": {}},
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    assert response.json()["error"]["code"] == -32602


@pytest.mark.django_db(transaction=True)
def test_unknown_method_is_32601(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc("nonsense/method", {}, session_id=initialized_session)
    assert response.status_code == 200, response.content
    assert response.json()["error"]["code"] == -32601


@pytest.mark.django_db(transaction=True)
def test_permission_denial_does_not_squat_on_32002(jsonrpc, initialized_session: str) -> None:
    """A denial must not look like a missing resource.

    ``-32002`` used to be this package's ``FORBIDDEN`` code, which meant a
    spec-following client read every permission denial as "resource not
    found". The denial now carries an implementation-defined code, and the
    HTTP status is what a client acts on.
    """
    response = jsonrpc(
        "tools/call",
        {"name": "conformance.gated", "arguments": {}},
        session_id=initialized_session,
    )
    assert response.status_code == 403, response.content
    assert response.json()["error"]["code"] != -32002


@pytest.mark.django_db(transaction=True)
def test_server_discover_needs_no_session_or_version_header(client) -> None:
    """The whole point of it is being the first thing a client sends.

    A modern client has no handshake to run and nothing to present, so gating
    discovery behind a session would leave it reachable only by clients that
    did not need it — and requiring a protocol-version header would mean naming
    a version to find out which versions are supported.
    """
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server/discover"}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"]
    assert "Mcp-Session-Id" not in response.headers


@pytest.mark.django_db(transaction=True)
def test_a_non_sessionless_method_still_requires_a_session(client) -> None:
    """Widening the gate for discovery must not have widened it for everything.

    **400, not 404.** The spec reserves ``404`` for a request *"containing
    that session ID"* after the server dropped it; a request carrying **no**
    header "SHOULD" get ``400 Bad Request``. This asserted 404 until the
    2026-08-05 spec read caught the conflation.
    """
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25"},
    )
    assert response.status_code == 400, response.content


def test_an_unknown_session_id_is_404_not_400(client) -> None:
    """The other half of the split: an id we don't honour *is* a 404.

    That is what tells a conforming client to re-``initialize``, so collapsing
    it into the 400 would break the documented recovery path.
    """
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": "no-such-session"},
    )
    assert response.status_code == 404, response.content
