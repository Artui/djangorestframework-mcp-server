from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from rest_framework_mcp.constants import ToolContentKind
from rest_framework_mcp.protocol.types.tool_content_block import ToolContentBlock

# RFC 3986 §3.1 scheme syntax. A resource link is a URI a host resolves, so it
# must be absolute; a bare string is not a link.
_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*$")

# Schemes refused in a ``resource_link``. A tool's payload is frequently a row
# an end user wrote — a bookmarks or attachments table — so the URI in it is
# untrusted input that a host may render as a clickable anchor or fetch to build
# a preview. These are the schemes where doing either executes script or reads
# something local:
#
# * ``javascript`` / ``vbscript`` — run in the host's origin on click.
# * ``data`` / ``blob`` — inline documents, which carry their own script.
# * ``file`` — the host machine's disk, not the server's resources.
# * ``about`` — the host's own internal pages.
#
# A denylist rather than an allowlist because a server's *own* resource URIs use
# whatever scheme it registered (``reports://``, ``docs://``, …) and this
# function cannot see the resource registry to enumerate them; refusing an
# unknown scheme would break the ordinary case to guard against nothing.
_REFUSED_LINK_SCHEMES = frozenset({"about", "blob", "data", "file", "javascript", "vbscript"})


def build_content_blocks(
    payload: Any,
    *,
    content_kind: ToolContentKind,
    mime_type: str | None,
) -> list[ToolContentBlock] | str:
    """Project a rendered tool payload into non-text ``content`` blocks.

    Returns the blocks, or an **explanatory message** when the payload does not
    match the kind the binding declared — a message rather than a response, so
    each caller wraps it in the envelope it is already building (as with
    ``enforce_result_bytes``).

    A mismatch is a server-side mistake that can only be caught here, since a
    callable's return type is not knowable at registration. It surfaces as a
    tool-level error rather than an exception so the client still gets a
    well-formed response.

    ``TEXT`` never reaches this function;
    [`build_tool_result`][rest_framework_mcp.output.tool_result.build_tool_result]
    handles it directly, along with the ``OutputFormat`` rendering only text blocks
    have."""
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

    A single mapping and a sequence of them are both accepted, tuples included:
    a tool resolving one document and one resolving several should not need
    different plumbing.
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
        refusal: str | None = _refuse_link_uri(item["uri"])
        if refusal is not None:
            return refusal
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


def _refuse_link_uri(uri: Any) -> str | None:
    """An explanatory message when ``uri`` must not be handed to a host, else ``None``.

    Same shape as the other mismatches here: the caller wraps it in the envelope
    it is already building, so the client gets a tool-level error rather than a
    block it should not have been sent.
    """
    if not isinstance(uri, str):
        return (
            "declares content_kind=RESOURCE_LINK but produced an entry whose "
            f"'uri' is {type(uri).__name__}, not a string. A resource link is a "
            "URI the client resolves."
        )
    try:
        scheme: str = urlsplit(uri).scheme
    except ValueError:
        # ``urlsplit`` raises on a malformed authority (an unclosed IPv6
        # literal). Unparseable is unlinkable.
        scheme = ""
    if not _URI_SCHEME_RE.match(scheme):
        return (
            "declares content_kind=RESOURCE_LINK but produced the relative or "
            f"schemeless 'uri' {uri!r}. A resource link must be an absolute URI, "
            "e.g. `https://example.test/doc` or a URI under one of this server's "
            "own registered resource schemes."
        )
    if scheme.lower() in _REFUSED_LINK_SCHEMES:
        return (
            f"declares content_kind=RESOURCE_LINK but produced the 'uri' {uri!r}. "
            f"`{scheme}:` links are never emitted: a host that renders or previews "
            "one would execute script or read its own machine, and a payload field "
            "holding a URI is usually value a caller stored. Emit an http(s) URI or "
            "one of this server's own resource URIs."
        )
    return None


__all__ = ["build_content_blocks"]
