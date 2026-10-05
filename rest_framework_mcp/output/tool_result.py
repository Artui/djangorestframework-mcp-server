from __future__ import annotations

from typing import Any

from rest_framework_services import UNSET

from rest_framework_mcp.constants import OutputFormat, ToolContentKind
from rest_framework_mcp.output.build_content_blocks import build_content_blocks
from rest_framework_mcp.output.encode_json import encode_json
from rest_framework_mcp.output.encode_toon import encode_toon
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.utils import toon_encoder
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
    """Build a [`ToolResult`][rest_framework_mcp.protocol.types.tool_result.ToolResult]
    for a successful (or tool-level error) call.

    Args:
        payload: The JSON-shaped tool output. Becomes ``structuredContent``
            verbatim and is also rendered as the first content block.
        output_format: How ``content[0]`` renders the payload. TOON output is
            wrapped in a fenced ``toon`` block with a leading marker line so
            clients that don't parse TOON natively can still display it. On a
            deployment without the optional ``[toon]`` extra the encoder falls
            back to JSON, and the marker is left off with it — the label always
            names the format the bytes are actually in.
        is_error: Stamped onto the result as ``isError``.
        include_structured_content: ``False`` omits ``structuredContent``
            entirely. The text block still carries the full payload, so a
            client that doesn't consume the structured field loses nothing.
        meta: The base protocol's ``_meta`` bundle on the *result envelope* —
            per-call, unlike the static ``_meta`` already advertised on the
            ``tools/list`` entry. Omitted from the payload when empty.
        content_kind: The block type the binding declared. Anything other than
            ``TEXT`` bypasses ``output_format`` entirely — there is no TOON
            rendering of a PNG — and a payload that doesn't match the declared
            kind comes back as an ``isError`` result naming the binding.
        content_mime_type: Media type for a non-``TEXT`` block.
        binding_name: Names the binding in that mismatch message.

    A ``None`` payload is a result with **nothing to present**: an
    ``allow_none`` RETRIEVE that found no row, or a service whose output
    re-read selector found none. It is served as ``{}``, both as
    ``structuredContent`` and as the text block, because MCP requires
    structured content to be an object and asks the text block to carry the
    same JSON. This is the one place the rule lives, so a selector, a service
    and a chain answer it the same way on every entry point. The advertised
    ``outputSchema`` of those tools admits the ``{}``
    (``build_output_schema(may_be_empty=...)``). A service with no re-read
    selector that returns ``None`` is served ``{}`` too, against a schema that
    still requires its fields: the documented limit, since nothing it declares
    says whether it can return ``None``.
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
            else UNSET,
            is_error=is_error,
            meta=meta,
        )

    # Only the text kind: a media or resource-link block has its own payload
    # contract, which ``build_content_blocks`` answers above.
    if payload is None:
        payload = {}
    resolved: OutputFormat = _resolve_format(payload, output_format)
    if resolved is OutputFormat.TOON:
        encoded: str = encode_toon(payload)
        # ``encode_toon`` warns and falls back to JSON where the optional extra
        # is absent, and that warning goes to the server's log, not onto the
        # wire. Stamping the marker regardless would hand the client a label
        # naming a format the bytes are not, which is worse than no label: a
        # pipeline that picks its parser from the marker line mis-parses. The
        # fallback therefore ships as plain, unmarked JSON.
        text = f"# format: toon\n```toon\n{encoded}\n```" if toon_encoder() is not None else encoded
    else:
        text = encode_json(payload)
    return ToolResult(
        content=[ToolContentBlock.text_block(text)],
        structured_content=payload if include_structured_content else UNSET,
        is_error=is_error,
        meta=meta,
    )


__all__ = ["build_tool_result"]
