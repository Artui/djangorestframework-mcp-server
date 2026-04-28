"""End-to-end prompts coverage through the live transport + capability advertisement."""

from __future__ import annotations

import json

from django.test import Client


def test_initialize_does_not_advertise_prompts_when_none_registered(jsonrpc) -> None:
    """The default test app has no prompts → ``capabilities.prompts`` is omitted."""
    response = jsonrpc(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.0"},
        },
        protocol_version=None,
    )
    body = response.json()
    capabilities = body["result"]["capabilities"]
    assert "prompts" not in capabilities


def test_initialize_advertises_prompts_when_registered(client: Client) -> None:
    """A server with prompts → ``capabilities.prompts`` is present.

    We register one through the running ``server`` instance from the testapp
    URL conf, then drive ``initialize`` against it.
    """
    from tests.testapp.urls import server

    server.register_prompt(name="hello.world", render=lambda **_: "hi")
    try:
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
                        "clientInfo": {"name": "x", "version": "0"},
                    },
                }
            ),
            content_type="application/json",
        )
        body = response.json()
        assert body["result"]["capabilities"]["prompts"] == {}
    finally:
        # Drop the prompt we registered so it doesn't leak into other tests.
        server.prompts._bindings.pop("hello.world", None)  # type: ignore[attr-defined]


def test_prompts_list_and_get_round_trip(jsonrpc, initialized_session: str) -> None:
    """Register a prompt, list it, get it through the live dispatch table."""
    from tests.testapp.urls import server

    server.register_prompt(
        name="echo",
        render=lambda *, who: f"Hello, {who}!",
        arguments=[
            __import__(
                "rest_framework_mcp.protocol.prompt_argument",
                fromlist=["PromptArgument"],
            ).PromptArgument(name="who", required=True)
        ],
    )
    try:
        list_resp = jsonrpc("prompts/list", {}, session_id=initialized_session)
        names = [p["name"] for p in list_resp.json()["result"]["prompts"]]
        assert "echo" in names

        get_resp = jsonrpc(
            "prompts/get",
            {"name": "echo", "arguments": {"who": "world"}},
            session_id=initialized_session,
        )
        body = get_resp.json()
        assert body["result"]["messages"][0]["content"]["text"] == "Hello, world!"
    finally:
        server.prompts._bindings.pop("echo", None)  # type: ignore[attr-defined]
