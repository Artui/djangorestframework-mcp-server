from __future__ import annotations

import base64
import binascii
from typing import Any

from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.types.request_metadata import RequestMetadata

METHOD_HEADER: str = "Mcp-Method"
NAME_HEADER: str = "Mcp-Name"
VERSION_HEADER: str = "Mcp-Protocol-Version"

_SENTINEL_PREFIX: str = "=?base64?"
_SENTINEL_SUFFIX: str = "?="

# Methods whose ``Mcp-Name`` mirrors a body field, and which field it is. A
# method absent from here sends no name header and must not be asked for one.
_NAME_SOURCES: dict[str, str] = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
    # The tasks extension mirrors ``taskId`` rather than a name, and for a
    # different reason: the core three let a gateway route on *what* is being
    # called, these let it route a follow-up to the instance holding that
    # task's state, which the extension notes "is typically required for
    # correctness". Omitting them would fail every conformant ``tasks/*``
    # request with a header mismatch.
    "tasks/get": "taskId",
    "tasks/update": "taskId",
    "tasks/cancel": "taskId",
}


def validate_modern_request(
    *,
    method: str,
    params: Any,
    metadata: RequestMetadata,
    headers: Any,
    supported_versions: tuple[str, ...],
) -> JsonRpcError | None:
    """Check a modern request's headers against its body. ``None`` if it passes.

    The transport mirrors selected body fields into headers so gateways and
    observability tooling can route without parsing JSON. That is only safe if
    the two agree: otherwise a gateway routes on the header while the server
    executes the body, the confused-deputy shape this closes. The spec is
    correspondingly strict — any mismatch, any missing required header, is
    ``400`` with ``-32020``.

    Three checks:

    1. The requested protocol version must be one this server implements —
       ``-32022``, carrying ``supported`` and ``requested`` so the client can
       retry without guessing.
    2. ``MCP-Protocol-Version`` must equal the ``_meta`` version.
    3. ``Mcp-Method`` must equal the body's method, and ``Mcp-Name`` — for the
       methods that have one — must equal ``params.name`` or ``params.uri``,
       **after** decoding the Base64 sentinel a client uses for any value that
       will not survive as a plain ASCII header.
    """
    if metadata.protocol_version not in supported_versions:
        return JsonRpcError(
            JsonRpcErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            data={
                "supported": list(supported_versions),
                "requested": metadata.protocol_version,
            },
        )

    version_header: Any = headers.get(VERSION_HEADER)
    if version_header != metadata.protocol_version:
        return _mismatch(
            f"{VERSION_HEADER} header value {version_header!r} does not match the "
            f"request body's protocol version {metadata.protocol_version!r}"
        )

    method_header: Any = headers.get(METHOD_HEADER)
    if method_header != method:
        return _mismatch(
            f"{METHOD_HEADER} header value {method_header!r} does not match the "
            f"request body's method {method!r}"
        )

    source: str | None = _NAME_SOURCES.get(method)
    if source is None:
        return None
    body_value: Any = params.get(source) if isinstance(params, dict) else None
    if not isinstance(body_value, str):
        # A missing or non-string source field is a params fault, not a header
        # one, and the handler owns that message — it knows which field and
        # why — so header validation stands aside.
        return None
    name_header: Any = headers.get(NAME_HEADER)
    if name_header is None:
        return _mismatch(f"{NAME_HEADER} header is required for {method!r} and is missing")
    decoded: str | None = _decode_header_value(name_header)
    if decoded is None:
        return _mismatch(f"{NAME_HEADER} header value is not valid Base64: {name_header!r}")
    if decoded != body_value:
        return _mismatch(
            f"{NAME_HEADER} header value {decoded!r} does not match the request "
            f"body's {source} {body_value!r}"
        )
    return None


def _decode_header_value(value: str) -> str | None:
    """Resolve the Base64 sentinel a client uses for header-unsafe values.

    HTTP field values are ASCII-only, so a tool named in another script or a
    URI with a space cannot ride as itself. The spec's answer is
    ``=?base64?<payload>?=``, and clients must use it for a *literal* value
    that happens to look like the sentinel too — which is why an unwrapped
    value is returned verbatim without further inspection.

    Returns ``None`` when the wrapper is present but the payload will not
    decode: a malformed header rather than a mismatched one, which the caller
    reports as such.
    """
    if not (value.startswith(_SENTINEL_PREFIX) and value.endswith(_SENTINEL_SUFFIX)):
        return value
    payload: str = value[len(_SENTINEL_PREFIX) : -len(_SENTINEL_SUFFIX)]
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def _mismatch(message: str) -> JsonRpcError:
    return JsonRpcError(JsonRpcErrorCode.HEADER_MISMATCH, f"Header mismatch: {message}")


__all__ = ["validate_modern_request"]
