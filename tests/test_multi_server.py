"""Two servers mounted in one project must be distinguishable and isolated.

The scenario the ``url_namespace`` knob was built for but which nothing
exercised: a project running ``/internal/mcp`` alongside ``/public/mcp``. The
per-server ``name`` was inert for exactly as long as no test asserted the wire
identity of a *second* mount.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client, override_settings

INTERNAL = "/internal/mcp/"
PUBLIC = "/public/mcp/"


@pytest.fixture
def multi_urlconf():
    with override_settings(ROOT_URLCONF="tests.testapp.multi_urls"):
        yield


def _initialize(client: Client, path: str) -> Any:
    return client.post(
        path,
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0.0"},
                },
            }
        ),
        content_type="application/json",
    )


def test_each_server_reports_its_own_identity(multi_urlconf) -> None:
    """The regression this wave exists for: ``name=`` reaches the wire.

    Before, both mounts echoed the global ``SERVER_INFO`` name, so a client had
    no way to tell two servers apart.
    """
    internal = _initialize(Client(), INTERNAL).json()["result"]
    public = _initialize(Client(), PUBLIC).json()["result"]

    assert internal["serverInfo"] == {
        "name": "internal-mcp",
        "version": "2.0.0",
        "title": "Internal Tools",
    }
    assert public["serverInfo"] == {"name": "public-mcp", "version": "1.0.0"}


def test_title_is_the_human_label_and_name_stays_the_identifier(multi_urlconf) -> None:
    """The spec's split: ``name`` is "intended for programmatic or logical use",
    ``title`` is "intended for UI and end-user contexts". A server with a title
    keeps its identifier — the title does not replace it."""
    result = _initialize(Client(), INTERNAL).json()["result"]

    assert result["serverInfo"]["name"] == "internal-mcp"
    assert result["serverInfo"]["title"] == "Internal Tools"


def test_title_omitted_when_not_given(multi_urlconf) -> None:
    """Optional per the spec — clients fall back to ``name``."""
    result = _initialize(Client(), PUBLIC).json()["result"]
    assert "title" not in result["serverInfo"]


def test_server_name_is_not_shadowed_by_the_server_info_setting(multi_urlconf) -> None:
    """An explicit ``name=`` outranks ``SERVER_INFO``.

    ``conftest_settings`` configures ``SERVER_INFO['name']``, which used to win
    unconditionally — the mechanism of the bug, pinned here directly.
    """
    result = _initialize(Client(), INTERNAL).json()["result"]
    assert result["serverInfo"]["name"] == "internal-mcp"


def test_description_surfaces_as_initialize_instructions(multi_urlconf) -> None:
    result = _initialize(Client(), INTERNAL).json()["result"]
    assert result["instructions"] == "Internal tools. Staff only."


def test_instructions_omitted_when_no_description_given(multi_urlconf) -> None:
    """The spec field is optional — a server without a description omits it
    rather than sending an empty string."""
    result = _initialize(Client(), PUBLIC).json()["result"]
    assert "instructions" not in result


def test_sessions_are_not_interchangeable_across_mounts(multi_urlconf) -> None:
    """A session minted at one mount must not authorize the other.

    Both servers here hold their own ``InMemorySessionStore``; this pins that
    the transport actually consults its *own* store rather than any shared
    namespace.
    """
    session_id = _initialize(Client(), PUBLIC)["Mcp-Session-Id"]
    assert session_id

    response = Client().post(
        INTERNAL,
        data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
        content_type="application/json",
        HTTP_MCP_SESSION_ID=session_id,
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    assert response.status_code == 404
