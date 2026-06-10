from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_mcp.output.encode_json import encode_json
from rest_framework_mcp.protocol.types.tool_content_block import ToolContentBlock
from rest_framework_mcp.protocol.types.tool_result import ToolResult


def build_error_tool_result(
    message: str,
    *,
    error_type: str,
    detail: Mapping[str, Any] | None = None,
) -> ToolResult:
    """Build an ``isError: true`` :class:`ToolResult` for a tool-level failure.

    Per the MCP spec, *execution* failures (business rules, validation the
    service performed on well-shaped input, missing rows) should come back
    as tool results the model can read and self-correct from — JSON-RPC
    protocol errors are reserved for faults in the call itself (malformed
    params shape, unknown tool, auth, rate limits).

    The error payload is rendered as JSON text in ``content[0]``:

    .. code-block:: json

        {"error": {"type": "service_error", "message": "...", "detail": {...}}}

    ``structuredContent`` is deliberately omitted — the spec ties it to the
    tool's ``outputSchema``, which describes the *success* shape; a strict
    client validating an error payload against it would reject the result.

    ``detail`` (when supplied) is merged into the ``error`` object — used
    for per-field validation detail and chain-tool ``failedStep`` markers.
    """
    error: dict[str, Any] = {"type": error_type, "message": message}
    if detail:
        error.update(detail)
    payload: dict[str, Any] = {"error": error}
    return ToolResult(
        content=[ToolContentBlock(type="text", text=encode_json(payload))],
        structured_content=None,
        is_error=True,
    )


__all__ = ["build_error_tool_result"]
