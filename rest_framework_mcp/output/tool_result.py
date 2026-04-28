from __future__ import annotations

from typing import Any

from rest_framework_mcp.output.encode_json import encode_json
from rest_framework_mcp.output.encode_toon import encode_toon
from rest_framework_mcp.output.format import OutputFormat
from rest_framework_mcp.protocol.tool_content_block import ToolContentBlock
from rest_framework_mcp.protocol.tool_result import ToolResult


def _is_uniform_list_of_objects(payload: Any) -> bool:
    """Heuristic for ``OutputFormat.AUTO``: TOON shines on uniform arrays.

    True when the payload is a non-empty list whose elements are all dicts
    with the same set of keys.
    """
    if not isinstance(payload, list) or not payload:
        return False
    if not all(isinstance(item, dict) for item in payload):
        return False
    first_keys: frozenset[str] = frozenset(payload[0].keys())
    return all(frozenset(item.keys()) == first_keys for item in payload[1:])


def _resolve_format(payload: Any, output_format: OutputFormat) -> OutputFormat:
    if output_format is not OutputFormat.AUTO:
        return output_format
    return OutputFormat.TOON if _is_uniform_list_of_objects(payload) else OutputFormat.JSON


def build_tool_result(
    payload: Any,
    *,
    output_format: OutputFormat = OutputFormat.JSON,
    is_error: bool = False,
) -> ToolResult:
    """Build a :class:`ToolResult` for a successful (or tool-level error) call.

    ``payload`` is the JSON-shaped tool output; it becomes ``structuredContent``
    verbatim and is also rendered as the first content block per
    ``output_format``. TOON output is wrapped in a fenced ``toon`` block with
    a leading marker line so clients that don't parse TOON natively can still
    display it.
    """
    resolved: OutputFormat = _resolve_format(payload, output_format)
    if resolved is OutputFormat.TOON:
        text = "# format: toon\n```toon\n" + encode_toon(payload) + "\n```"
    else:
        text = encode_json(payload)
    return ToolResult(
        content=[ToolContentBlock(type="text", text=text)],
        structured_content=payload,
        is_error=is_error,
    )


__all__ = ["build_tool_result"]
