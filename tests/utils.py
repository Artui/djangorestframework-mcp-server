"""Shared assertions for the test suite."""

from __future__ import annotations

import json
from typing import Any


def tool_error(out: Any) -> dict[str, Any]:
    """Assert ``out`` is an ``isError`` tool result; return its error object.

    Tool-level failures (business rules, service-raised validation,
    missing rows) come back as successful JSON-RPC responses whose result
    carries ``isError: true`` and a JSON error payload in ``content[0]``.
    ``structuredContent`` must be absent — it is tied to the success
    ``outputSchema``.
    """
    assert isinstance(out, dict), f"expected a tool-result dict, got {out!r}"
    assert out.get("isError") is True
    assert "structuredContent" not in out
    return json.loads(out["content"][0]["text"])["error"]
