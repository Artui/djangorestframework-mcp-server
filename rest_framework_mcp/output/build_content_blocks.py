from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_mcp.constants import ToolContentKind
from rest_framework_mcp.protocol.types.tool_content_block import ToolContentBlock


def build_content_blocks(
    payload: Any,
    *,
    content_kind: ToolContentKind,
    mime_type: str | None,
) -> list[ToolContentBlock] | str:
    """Project a rendered tool payload into non-text ``content`` blocks.

    Returns the blocks, or an **explanatory message** when the payload does not
    match the kind the binding declared. Returning a message rather than a
    response mirrors :func:`enforce_result_bytes`: the caller knows which
    envelope it is building, and the two callers here want different ones.

    A mismatch is always a server-side mistake — the binding says ``IMAGE`` and
    the service returned a dict — but it can only be caught here, because a
    callable's return type is not knowable at registration. It surfaces as a
    tool-level error rather than an exception so the client still gets a
    well-formed response, and the message names the declaration that is wrong
    rather than the value that tripped over it.

    ``TEXT`` never reaches this function; :func:`build_tool_result` handles it
    directly, since it also owns the ``OutputFormat`` rendering that only text
    blocks have.
    """
    if content_kind is ToolContentKind.RESOURCE_LINK:
        return _resource_links(payload)
    # IMAGE / AUDIO — the payload is the media itself.
    if not isinstance(payload, bytes | bytearray | memoryview | str):
        return (
            f"declares content_kind={content_kind.name} but produced "
            f"{type(payload).__name__}. Media content is the bytes themselves "
            "(or a str already in base64), not a JSON-shaped value — an "
            "output_serializer on this binding is probably rendering it away."
        )
    factory = (
        ToolContentBlock.image if content_kind is ToolContentKind.IMAGE else ToolContentBlock.audio
    )
    # ``mime_type`` is guaranteed non-None by the binding's own validation:
    # declaring IMAGE / AUDIO without one is refused at registration.
    return [factory(_as_bytes(payload), mime_type=mime_type or "")]


def _as_bytes(payload: bytes | bytearray | memoryview | str) -> bytes | str:
    """Normalise the buffer protocol's variants; leave ``str`` alone."""
    if isinstance(payload, bytearray | memoryview):
        return bytes(payload)
    return payload


def _resource_links(payload: Any) -> list[ToolContentBlock] | str:
    """One ``resource_link`` per mapping in the payload.

    A single mapping and a sequence of them are both accepted — a tool that
    resolves one document and one that resolves several should not need
    different plumbing. Tuples count: a selector returning one is producing the
    right shape, and rejecting it with a message about the wrong shape would
    read as nonsense.
    """
    items: Any = [payload] if isinstance(payload, Mapping) else payload
    if not isinstance(items, list | tuple) or not all(isinstance(item, Mapping) for item in items):
        return (
            "declares content_kind=RESOURCE_LINK but produced "
            f"{type(payload).__name__}. Resource links are described by a "
            "mapping with 'uri' and 'name' (or a list of them), not by the "
            "resource contents themselves."
        )
    blocks: list[ToolContentBlock] = []
    for item in items:
        if "uri" not in item or "name" not in item:
            return (
                "declares content_kind=RESOURCE_LINK but produced an entry "
                f"missing 'uri' and/or 'name': {sorted(item)}. Both are "
                "required — a client has nothing to fetch or label without them."
            )
        blocks.append(
            ToolContentBlock.resource_link(
                item["uri"],
                name=item["name"],
                description=item.get("description"),
                mime_type=item.get("mimeType"),
                annotations=item.get("annotations"),
            )
        )
    return blocks


__all__ = ["build_content_blocks"]
