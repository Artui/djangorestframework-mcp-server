from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.test import Client, override_settings

from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from tests.testapp.mcp import build_server
from tests.testapp.urlconf_for import urlconf_for

# Scalars are resolved once, in ``MCPServer.__init__`` — so a test that needs
# non-default scalars mounts its own server rather than mutating settings around
# the shared one (which is built when its URL conf is first imported, and would
# both ignore the change and leak it into every later test in the process).


def test_post_with_too_large_body(client: Client) -> None:
    server = build_server(config=build_mcp_config(allowed_origins=["*"], max_request_bytes=10))
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        response = client.post("/mcp/", data=b"X" * 1024, content_type="application/json")
    assert response.status_code == 413


def test_post_with_invalid_json(client: Client) -> None:
    response = client.post(
        "/mcp/",
        data="not json",
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    body = response.json()
    assert body["error"]["code"] == -32700


# From Python 3.14 the decoder's recursion is bounded by the C stack rather than
# a count, so how deep a body must nest to raise depends on the stack the parse
# runs on: about 74,000 levels on an 8 MiB main thread, and about 600,000 under
# ``make``, which raises the soft stack limit to the hard one (64 MiB on macOS)
# for the processes it starts. A thread's stack is the size it was created with,
# so on this one the limit is about 37,000 levels on 3.14 and the count, about
# 1,000 or 10,000, on earlier Pythons: 100,000 is past it wherever the suite runs.
_BOUNDED_STACK_BYTES: int = 4 * 1024 * 1024


def _on_a_bounded_stack(call: Callable[[], Any]) -> Any:
    """Run ``call`` on a fresh thread with a 4 MiB stack and return its result."""
    previous: int = threading.stack_size(_BOUNDED_STACK_BYTES)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(call).result()
    finally:
        threading.stack_size(previous)


def test_a_decode_error_keeps_the_decoders_detail(client: Client) -> None:
    """A ``JSONDecodeError`` names what it expected, and the refusal carries it."""
    response = client.post(
        "/mcp/",
        data="not json",
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    assert response.json()["error"]["message"] == "Invalid JSON: Expecting value"


@pytest.mark.parametrize(
    ("body", "raises"),
    [
        # Past the decoder's recursion limit on the bounded stack, on every
        # supported Python. 200 KB, under the 1 MiB default cap.
        pytest.param(
            b"[" * 100_000 + b"]" * 100_000, RecursionError, id="nested-past-the-recursion-limit"
        ),
        # Over the 4300-digit cap on int conversion: a plain ``ValueError``.
        pytest.param(
            b'{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"n": ' + b"9" * 5000 + b"}}",
            ValueError,
            id="integer-over-the-digit-limit",
        ),
        # Not UTF-8: a ``UnicodeDecodeError`` from decoding the bytes.
        pytest.param(
            b'{"jsonrpc": "2.0", "id": 1, "method": "\xff"}',
            UnicodeDecodeError,
            id="invalid-utf-8",
        ),
    ],
)
def test_a_body_json_cannot_decode_is_a_parse_error(
    client: Client, body: bytes, raises: type[Exception]
) -> None:
    """None of these is a ``JSONDecodeError``, and each escaped as a 500.

    The parse runs before authentication, so anyone could send one. The id is
    ``null`` because a body that never decoded has no id to echo. The request
    runs on a bounded stack so the nested body raises however large the stack
    the suite was started with.
    """

    def post() -> Any:
        # Checked on the thread that decodes it, because that thread's stack
        # is what the nested body's limit depends on. A body that decoded here
        # would be answered by a later check and pass without the fix.
        with pytest.raises(raises) as raised:
            json.loads(body)
        assert raised.type is raises
        return client.post(
            "/mcp/",
            data=body,
            content_type="application/json",
            HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        )

    response = _on_a_bounded_stack(post)
    payload: Any = response.json()
    # Neither the size cap, which runs before the parse, nor the shape check
    # a body that decoded would reach after it.
    assert payload["error"]["message"] != "Request body too large"
    assert payload["error"]["message"] != "JSON-RPC message must be a JSON object"
    assert response.status_code == 400
    assert payload == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Invalid JSON: body could not be decoded"},
    }


def test_post_with_invalid_jsonrpc_shape(client: Client) -> None:
    response = client.post(
        "/mcp/",
        data=json.dumps({"foo": "bar"}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    body = response.json()
    assert body["error"]["code"] == -32600


def test_origin_not_allowed_returns_403(client: Client) -> None:
    server = build_server(config=build_mcp_config(allowed_origins=["https://allowed.example"]))
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        response = client.post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            content_type="application/json",
            HTTP_ORIGIN="https://blocked.example",
            HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        )
    assert response.status_code == 403


def test_get_blocked_origin_returns_403(client: Client) -> None:
    server = build_server(config=build_mcp_config(allowed_origins=["https://allowed.example"]))
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        response = client.get("/mcp/", HTTP_ORIGIN="https://blocked.example")
    assert response.status_code == 403


def test_delete_without_session_id_is_204(client: Client) -> None:
    response = client.delete("/mcp/")
    assert response.status_code == 204


def test_delete_blocked_origin_returns_403(client: Client) -> None:
    server = build_server(config=build_mcp_config(allowed_origins=["https://allowed.example"]))
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        response = client.delete("/mcp/", HTTP_ORIGIN="https://blocked.example")
    assert response.status_code == 403


def test_two_servers_can_allow_different_origins(client: Client) -> None:
    """The payoff: an origin allowed by one mount is refused by the other."""
    internal = build_server(config=build_mcp_config(allowed_origins=["https://internal.example"]))
    public = build_server(config=build_mcp_config(allowed_origins=["https://public.example"]))

    with override_settings(ROOT_URLCONF=urlconf_for(internal)):
        allowed = client.get("/mcp/", HTTP_ORIGIN="https://internal.example")
    with override_settings(ROOT_URLCONF=urlconf_for(public)):
        refused = client.get("/mcp/", HTTP_ORIGIN="https://internal.example")

    assert allowed.status_code != 403
    assert refused.status_code == 403


def test_post_response_jsonrpc_id_with_jsonrpc_request_only(client: Client) -> None:
    """A bare JSON-RPC response object posted to the server is rejected."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    body = response.json()
    assert body["error"]["code"] == -32600


def test_post_with_list_params_treated_as_no_params(
    client: Client, initialized_session: str
) -> None:
    """JSON-RPC list-shaped params are silently coerced to None for MCP methods."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1, 2]}),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        HTTP_MCP_SESSION_ID=initialized_session,
    )
    body = response.json()
    assert "result" in body


def test_initialize_with_unsupported_protocol_header_is_rejected(client: Client) -> None:
    server = build_server(
        config=build_mcp_config(allowed_origins=["*"], protocol_versions=["2025-11-25"])
    )
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        response = client.post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "x", "version": "1"},
                    },
                }
            ),
            content_type="application/json",
            HTTP_MCP_PROTOCOL_VERSION="9999-99-99",
        )
    # ``initialize`` may omit the header; it may not name a version this
    # server does not speak. Falling back would answer a different protocol
    # than the one asked for and say nothing about it.
    assert response.status_code == 400
