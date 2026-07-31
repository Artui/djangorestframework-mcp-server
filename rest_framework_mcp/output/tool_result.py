from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import OutputFormat, ToolContentKind
from rest_framework_mcp.output.build_content_blocks import build_content_blocks
from rest_framework_mcp.output.encode_json import encode_json
from rest_framework_mcp.output.encode_toon import encode_toon
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.protocol.types.tool_content_block import ToolContentBlock
from rest_framework_mcp.protocol.types.tool_result import ToolResult


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
    include_structured_content: bool = True,
    meta: dict[str, Any] | None = None,
    content_kind: ToolContentKind = ToolContentKind.TEXT,
    content_mime_type: str | None = None,
    binding_name: str | None = None,
) -> ToolResult:
    """Build a :class:`ToolResult` for a successful (or tool-level error) call.

    ``payload`` is the JSON-shaped tool output; it becomes ``structuredContent``
    verbatim and is also rendered as the first content block per
    ``output_format``. TOON output is wrapped in a fenced ``toon`` block with
    a leading marker line so clients that don't parse TOON natively can still
    display it.

    When ``include_structured_content`` is ``False``, the
    ``structuredContent`` field is omitted from the response. The text
    rendering in ``content[0]`` still carries the full payload, so clients
    that don't consume the structured field lose nothing.

    ``meta`` is the base protocol's generic ``_meta`` bundle on the *result
    envelope* — genuinely per-call, so it is a parameter here rather than
    something sourced from the binding: a tool's static ``_meta`` is already
    advertised on its ``tools/list`` entry and repeating it on every result
    would be redundant. Omitted from the payload when empty.

    ``content_kind`` selects the block type, per the binding's declaration.
    Anything other than ``TEXT`` bypasses ``output_format`` entirely — there is
    no TOON rendering of a PNG — and a payload that doesn't match the declared
    kind comes back as an ``isError`` result naming the binding, which is the
    same treatment an oversized result or a missed deadline gets.
    ``binding_name`` exists only to make that message actionable.
    """
    if content_kind is not ToolContentKind.TEXT:
        blocks = build_content_blocks(
            payload, content_kind=content_kind, mime_type=content_mime_type
        )
        if isinstance(blocks, str):
            label = f"Tool {binding_name!r}" if binding_name else "This tool"
            return build_error_tool_result(f"{label} {blocks}", error_type="output_encoding")
        return ToolResult(
            content=blocks,
            # Media blocks carry no ``structuredContent`` — binary is not JSON.
            # Resource links do: the links themselves are an ordinary JSON
            # payload, so the model gets both projections of the same answer.
            structured_content=payload
            if include_structured_content and content_kind is ToolContentKind.RESOURCE_LINK
            else None,
            is_error=is_error,
            meta=meta,
        )

    resolved: OutputFormat = _resolve_format(payload, output_format)
    if resolved is OutputFormat.TOON:
        text = "# format: toon\n```toon\n" + encode_toon(payload) + "\n```"
    else:
        text = encode_json(payload)
    return ToolResult(
        content=[ToolContentBlock.text_block(text)],
        structured_content=payload if include_structured_content else None,
        is_error=is_error,
        meta=meta,
    )


__all__ = ["build_tool_result"]
