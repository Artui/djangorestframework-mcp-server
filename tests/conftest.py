from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client


@pytest.fixture
def client() -> Client:
    return Client()


def post_jsonrpc(
    client: Client,
    *,
    method: str,
    params: dict[str, Any] | None = None,
    request_id: int | str | None = 1,
    session_id: str | None = None,
    protocol_version: str | None = "2025-11-25",
    is_notification: bool = False,
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not is_notification:
        body["id"] = request_id
    headers: dict[str, str] = {}
    if session_id is not None:
        headers["HTTP_MCP_SESSION_ID"] = session_id
    if protocol_version is not None:
        headers["HTTP_MCP_PROTOCOL_VERSION"] = protocol_version
    return client.post(
        "/mcp/",
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )


@pytest.fixture
def jsonrpc(client: Client):
    def _post(
        method: str,
        params: dict[str, Any] | None = None,
        *,
        request_id: int | str | None = 1,
        session_id: str | None = None,
        protocol_version: str | None = "2025-11-25",
        is_notification: bool = False,
    ):
        return post_jsonrpc(
            client,
            method=method,
            params=params,
            request_id=request_id,
            session_id=session_id,
            protocol_version=protocol_version,
            is_notification=is_notification,
        )

    return _post


@pytest.fixture
def initialized_session(client: Client) -> str:
    """Run an ``initialize`` and return the issued session id."""
    response = post_jsonrpc(
        client,
        method="initialize",
        params={
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.0"},
        },
        protocol_version=None,
    )
    assert response.status_code == 200
    return response["Mcp-Session-Id"]
