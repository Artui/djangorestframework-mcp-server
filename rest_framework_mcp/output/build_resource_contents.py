from __future__ import annotations

from typing import Any

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
) -> ResourceContents | JsonRpcError:
    """Render a selector's return value into one ``resources/read`` block.

    Shared by the sync and async read handlers. They are full parallel
    implementations rather than wrappers, so anything that varies by binding
    lives here — otherwise the two transports drift and only one of them gets
    a fix.

    Two steps, in order:

    1. **Render**, if the binding declares an ``output_serializer`` — with
       ``many=True`` for a ``LIST`` selector, ``many=False`` otherwise.
    2. **Encode** per the binding's :class:`ResourceEncoding`. ``JSON``
       pretty-prints; ``TEXT`` passes the value straight through as the body,
       which is what an HTML / Markdown / CSV resource needs — JSON-encoding
       one of those yields a quoted string literal rather than the document.

    A ``TEXT`` binding whose selector returned something other than a ``str``
    is a server misconfiguration that can only surface at read time (the
    selector's return type isn't knowable at registration). It comes back as a
    :class:`JsonRpcError` rather than an exception, so the client gets a
    well-formed error response instead of a transport-level 500.
    """
    payload: Any = raw
    if binding.output_serializer is not None:
        payload = binding.output_serializer(raw, many=binding.kind is SelectorKind.LIST).data

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
