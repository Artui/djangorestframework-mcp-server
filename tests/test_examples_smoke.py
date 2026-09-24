"""Smoke test: the tracked examples import + build against the shipped API.

The examples are standalone Django projects (their own settings + apps), so each
is exercised in a subprocess with its own ``DJANGO_SETTINGS_MODULE`` rather than
imported into this suite's Django context. ``build_server()`` performs every
tool / resource / prompt registration, so a stale API surfaces here as a
non-zero exit — the guard that would have caught the 0.8 ``filter_set=`` /
missing-``kind`` breakage.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_EXAMPLES = [
    pytest.param("invoicing", "invoicing.settings", "invoices.mcp", id="invoicing"),
    pytest.param("job_status", "job_status.settings", "jobs.mcp", id="job_status"),
]


@pytest.mark.parametrize(("project", "settings_module", "mcp_module"), _EXAMPLES)
def test_example_build_server(project: str, settings_module: str, mcp_module: str) -> None:
    root = _EXAMPLES_DIR / project
    code = f"import django; django.setup()\nimport {mcp_module} as m\nm.build_server()"
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": settings_module}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# Runs inside the invoicing project: an in-memory database, three invoices, and
# two ``tools/call`` posts over the real JSON-RPC endpoint the project mounts.
# The replies are printed as JSON for the test to read, so the assertions stay
# here rather than in a string.
_INVOICING_FIELD_SELECTION = textwrap.dedent(
    """
    import json

    import django
    from django.conf import settings

    # Before setup, so no connection has read the on-disk name yet.
    settings.DATABASES["default"]["NAME"] = ":memory:"
    django.setup()

    from django.core.management import call_command
    from django.test import Client

    from invoices.models import Invoice

    call_command("migrate", verbosity=0)
    for number in ("INV-A", "INV-B", "INV-C"):
        Invoice.objects.create(number=number, amount_cents=100)

    client = Client(HTTP_ORIGIN="http://localhost")
    initialized = client.post(
        "/mcp/",
        data=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0"},
            },
        }),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
    )
    headers = {
        "HTTP_MCP_PROTOCOL_VERSION": "2025-11-25",
        "HTTP_MCP_SESSION_ID": initialized["Mcp-Session-Id"],
    }

    def call(arguments):
        response = client.post(
            "/mcp/",
            data=json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "invoices.list", "arguments": arguments},
            }),
            content_type="application/json",
            **headers,
        )
        return {"status": response.status_code, "body": response.json()}

    print(json.dumps({
        "rows": call({"fields": "number", "limit": 2}),
        "envelope": call({"fields": "items"}),
    }))
    """
)


def test_invoicing_field_selection_is_per_item_and_refuses_the_envelope() -> None:
    """The example's paged list tool, selected per row and then against the envelope.

    Not only that the example runs: that it demonstrates what it claims. A row
    selection narrows each item, and a selection written against the page
    envelope (the shape the tool's ``outputSchema`` shows) is an ``isError``
    ``validation_error`` naming the argument, where it used to escape as a bare
    DRF body that no JSON-RPC client could parse.
    """
    result = subprocess.run(
        [sys.executable, "-c", _INVOICING_FIELD_SELECTION],
        cwd=_EXAMPLES_DIR / "invoicing",
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "invoicing.settings"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    replies = json.loads(result.stdout)

    rows = replies["rows"]
    assert rows["status"] == 200
    page = rows["body"]["result"]["structuredContent"]
    assert [set(item) for item in page["items"]] == [{"number"}, {"number"}]
    assert page["hasNext"] is True

    envelope = replies["envelope"]
    assert envelope["status"] == 200
    assert envelope["body"]["id"] == 2
    tool_result = envelope["body"]["result"]
    assert tool_result["isError"] is True
    error = json.loads(tool_result["content"][0]["text"])["error"]
    assert error["type"] == "validation_error"
    assert error["detail"] == {"fields": ["`items` field is not found"]}
    assert error["message"].startswith(
        "`fields` was rejected while rendering the result: `items` field is not found."
    )
    assert "never to the page envelope" in error["message"]
