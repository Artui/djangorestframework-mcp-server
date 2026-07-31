from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.types.resource_contents import ResourceContents


@dataclass(frozen=True)
class ToolContentBlock:
    """One entry in a :class:`ToolResult`'s ``content`` array.

    The spec models content as a five-member union — ``text``, ``image``,
    ``audio``, ``resource_link``, ``resource`` — whose members share no fields
    beyond ``type``, ``annotations`` and ``_meta``. This is one dataclass with
    every member's fields optional rather than five dataclasses behind a union,
    because ``content`` is a wire boundary: the shape is decided by ``type``,
    :meth:`to_dict` emits only what is set, and a union type would buy nothing
    a reader of the JSON can see.

    **Construct through the classmethods, not the initialiser.** Each one takes
    exactly the fields its block type requires, which is where the spec's rules
    live — an image without a ``mimeType`` or a ``resource_link`` without a
    ``uri`` is not a block a client can do anything with, and the constructors
    make those unrepresentable rather than merely discouraged. (Same reasoning
    as :meth:`ToolDefinition.service` / :meth:`ToolDefinition.selector`.) Two
    are named around a field they would otherwise shadow — ``text_block`` and
    ``embedded_resource``, for the ``text`` and ``resource`` fields.

    ``annotations`` carry ``audience`` / ``priority`` / ``lastModified`` — the
    identical bundle resources use, which is the spec's own choice and the
    reason it stays a free-form dict here.
    """

    type: str
    text: str | None = None
    data: str | None = None
    mime_type: str | None = None
    # ``resource_link`` is a ``Resource`` with a ``type`` — it carries the
    # resource's own descriptive fields rather than a payload.
    uri: str | None = None
    name: str | None = None
    description: str | None = None
    # ``resource`` embeds the contents themselves, which is the one place a
    # block nests another wire type rather than flattening it.
    resource: ResourceContents | None = None
    annotations: dict[str, Any] | None = None
    # Base-protocol ``_meta`` bundle. Free-form dict at this wire boundary
    # because ``_meta`` is MCP's open extension namespace (see
    # :class:`~rest_framework_mcp.protocol.types.tool.Tool`).
    meta: dict[str, Any] | None = None

    @classmethod
    def text_block(
        cls,
        text: str,
        *,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """A plain text block — what every tool result carries today."""
        return cls(type="text", text=text, annotations=annotations, meta=meta)

    @classmethod
    def image(
        cls,
        data: bytes | str,
        *,
        mime_type: str,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """An image block. ``data`` may be raw bytes or an existing base64 string.

        ``mime_type`` is required by the spec, not merely recommended: a client
        has no other way to know how to decode the bytes.
        """
        return cls(
            type="image",
            data=_encode_base64(data),
            mime_type=mime_type,
            annotations=annotations,
            meta=meta,
        )

    @classmethod
    def audio(
        cls,
        data: bytes | str,
        *,
        mime_type: str,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """An audio block — the same contract as :meth:`image`."""
        return cls(
            type="audio",
            data=_encode_base64(data),
            mime_type=mime_type,
            annotations=annotations,
            meta=meta,
        )

    @classmethod
    def resource_link(
        cls,
        uri: str,
        *,
        name: str,
        description: str | None = None,
        mime_type: str | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """A pointer to a resource the client can read separately.

        ⭐ The cheapest way for a tool here to return something large or
        non-JSON: the URI is one this server's own ``resources/read`` already
        serves, so no bytes ride on the tool-result path and the client fetches
        only what it decides it wants. The spec notes a linked resource need
        not appear in ``resources/list``.
        """
        return cls(
            type="resource_link",
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
            annotations=annotations,
            meta=meta,
        )

    @classmethod
    def embedded_resource(
        cls,
        resource: ResourceContents,
        *,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolContentBlock:
        """A resource's contents inlined into the result.

        Prefer :meth:`resource_link` unless the client genuinely cannot make a
        second round trip — embedding spends the caller's context on bytes it
        may not need.
        """
        return cls(type="resource", resource=resource, annotations=annotations, meta=meta)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.text is not None:
            out["text"] = self.text
        if self.data is not None:
            out["data"] = self.data
        if self.uri is not None:
            out["uri"] = self.uri
        if self.name is not None:
            out["name"] = self.name
        if self.description is not None:
            out["description"] = self.description
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.resource is not None:
            out["resource"] = self.resource.to_dict()
        if self.annotations is not None:
            out["annotations"] = self.annotations
        if self.meta:
            out["_meta"] = self.meta
        return out


def _encode_base64(data: bytes | str) -> str:
    """Base64-encode binary payloads; pass strings through unchanged.

    A ``str`` is taken to be base64 already — the alternative reading, "text to
    be encoded", would silently double-encode anyone who did the encoding
    themselves, and there is no way to tell the two apart by inspection.

    Lives beside the block rather than in ``output/`` because the block's own
    constructors are its only callers and the rule is part of their contract.
    """
    if isinstance(data, str):
        return data
    return base64.b64encode(data).decode("ascii")


__all__ = ["ToolContentBlock"]
