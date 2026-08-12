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

    True for a non-empty list whose elements are all dicts sharing one key set.
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

    Args:
        payload: The JSON-shaped tool output. Becomes ``structuredContent``
            verbatim and is also rendered as the first content block.
        output_format: How ``content[0]`` renders the payload. TOON output is
            wrapped in a fenced ``toon`` block with a leading marker line so
            clients that don't parse TOON natively can still display it.
        is_error: Stamped onto the result as ``isError``.
        include_structured_content: ``False`` omits ``structuredContent``. The
            text block still carries the full payload, so a client that doesn't
            consume the structured field loses nothing.
        meta: The base protocol's ``_meta`` bundle on the *result envelope* —
            per-call, unlike the static ``_meta`` already advertised on the
            ``tools/list`` entry. Omitted from the payload when empty.
        content_kind: The block type the binding declared. Anything other than
            ``TEXT`` bypasses ``output_format`` entirely — there is no TOON
            rendering of a PNG — and a payload that doesn't match the declared
            kind comes back as an ``isError`` result naming the binding.
        content_mime_type: Media type for a non-``TEXT`` block.
        binding_name: Names the binding in that mismatch message.
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
            # Resource links do: the links are an ordinary JSON payload.
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
