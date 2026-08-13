from __future__ import annotations

import base64
from typing import Any

from rest_framework_services import base_serializer_context
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.constants import JsonRpcErrorCode, ResourceEncoding
from rest_framework_mcp.output.encode_json import encode_json
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.resource_contents import ResourceContents
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding


def build_resource_contents(
    *,
    binding: ResourceBinding,
    uri: str,
    raw: Any,
    view: Any = None,
    request: Any = None,
) -> ResourceContents | JsonRpcError:
    """Render a selector's return value into one ``resources/read`` block.

    Shared by the sync and async read handlers, which are full parallel
    implementations rather than wrappers — so anything that varies by binding
    lives here, or the two transports drift and only one gets a fix.

    Two steps, in order:

    1. **Render**, if the binding declares an ``output_serializer`` — with
       ``many=True`` for a ``LIST`` selector. The serializer gets DRF's baseline
       context (``request`` / ``format`` / ``view``) built from the synthesized
       ``view`` / ``request``, so a field reading ``self.context["request"]``
       resolves it as it would behind a view. A resource binding has no context
       provider of its own: its selector is a bare callable, not a spec.
    2. **Encode** per the binding's
       [`ResourceEncoding`][rest_framework_mcp.constants.ResourceEncoding].
       ``JSON`` pretty-prints; ``TEXT`` passes the value through verbatim, which is what
       an HTML / Markdown / CSV resource needs; ``BLOB`` base64-encodes it into
       the spec's ``blob`` field. ``text`` and ``blob`` are mutually exclusive
       on a ``contents`` entry, so exactly one is ever set.

    A ``TEXT`` or ``BLOB`` binding whose selector returned the wrong Python type can
    only surface at read time, so it comes back as a
    [`JsonRpcError`][rest_framework_mcp.protocol.types.json_rpc_error.JsonRpcError]
    rather than an exception — a well-formed error response instead of a 500."""
    payload: Any = raw
    if binding.output_serializer is not None:
        payload = binding.output_serializer(
            raw,
            many=binding.kind is SelectorKind.LIST,
            context=base_serializer_context(view=view, request=request),
        ).data

    if binding.encoding is ResourceEncoding.BLOB:
        if not isinstance(payload, bytes | bytearray | memoryview):
            return JsonRpcError(
                JsonRpcErrorCode.INTERNAL_ERROR,
                f"Resource {binding.name!r} declares encoding=BLOB but produced "
                f"{type(payload).__name__}, not bytes. A BLOB resource's body is "
                "the binary itself, base64-encoded here, so the selector (after "
                "any output_serializer) must return the bytes.",
            )
        return ResourceContents(
            uri=uri,
            mime_type=binding.mime_type,
            blob=base64.b64encode(payload).decode("ascii"),
            meta=dict(binding.meta) or None,
        )

    text: str
    if binding.encoding is ResourceEncoding.TEXT:
        if not isinstance(payload, str):
            return JsonRpcError(
                JsonRpcErrorCode.INTERNAL_ERROR,
                f"Resource {binding.name!r} declares encoding=TEXT but produced "
                f"{type(payload).__name__}, not str. A TEXT resource's body is "
                "returned verbatim, so the selector (after any output_serializer) "
                "must return the document itself.",
            )
        text = payload
    else:
        text = encode_json(payload)

    return ResourceContents(
        uri=uri,
        mime_type=binding.mime_type,
        text=text,
        meta=dict(binding.meta) or None,
    )


__all__ = ["build_resource_contents"]
