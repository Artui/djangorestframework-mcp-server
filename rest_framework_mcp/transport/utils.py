"""Shared helpers for the sync and async streamable-HTTP viewsets."""

from __future__ import annotations

from typing import Any

from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


def is_permission_denial(result: Any) -> bool:
    """Whether a dispatch result is a permission denial needing HTTP 403.

    The MCP authorization spec's error table is normative: ``401`` for
    "authorization required or token invalid", ``403`` for "invalid scopes
    or insufficient permissions". A denial is therefore *not* a plain
    JSON-RPC error riding inside a ``200`` — the status carries meaning a
    client acts on.
    """
    return isinstance(result, JsonRpcError) and result.code == JsonRpcErrorCode.FORBIDDEN


def insufficient_scope_challenge(result: JsonRpcError, backend: MCPAuthBackend) -> str:
    """Build the ``WWW-Authenticate`` challenge for a permission denial.

    RFC 6750 §3.1 defines ``insufficient_scope`` plus a ``scope`` parameter
    listing what would satisfy the request — which is how a client knows
    what to ask for on the next authorization round rather than retrying
    the same token. This is what ``www_authenticate_challenge(scopes=...)``
    was always for; nothing called it with scopes until now, so the
    parameter sat dead while the denial went out as a bare ``200``.

    A denial with no scopes attached (a non-scope permission such as
    ``DjangoPermRequired``) still gets a 403, but no ``error=`` /
    ``scope=`` — RFC 6750 defines neither for that case, and inventing a
    scope the client cannot obtain would send it round a pointless loop.
    """
    data: Any = result.data if isinstance(result.data, dict) else {}
    scopes: list[str] | None = data.get("requiredScopes") or None
    return backend.www_authenticate_challenge(
        scopes=scopes, error="insufficient_scope" if scopes else None
    )


def modern_error_status(error: JsonRpcError) -> int:
    """HTTP status for a JSON-RPC error on the modern transport.

    The modern revision makes several statuses normative, and each one carries
    information the JSON-RPC code alone does not:

    - ``404`` for an unknown method. This is what lets a client tell a modern
      MCP endpoint from a legacy HTTP+SSE server that does not host one — both
      answer ``404``, but only ours carries a JSON-RPC ``-32601`` body, and the
      spec's fallback algorithm reads exactly that.
    - ``400`` for the three spec-reserved rejections (header mismatch,
      unsupported version, missing client capability). A client that gets a
      ``400`` inspects the body before falling back to ``initialize``: a
      recognised modern error means "fix the request", anything else means
      "this server is legacy".
    - ``403`` for a permission denial, as in the legacy era.

    Everything else rides inside a ``200``, which is ordinary JSON-RPC.
    """
    if error.code == JsonRpcErrorCode.METHOD_NOT_FOUND:
        return 404
    if error.code in _MODERN_BAD_REQUEST_CODES:
        return 400
    if error.code == JsonRpcErrorCode.FORBIDDEN:
        return 403
    return 200


_MODERN_BAD_REQUEST_CODES: frozenset[int] = frozenset(
    {
        JsonRpcErrorCode.HEADER_MISMATCH,
        JsonRpcErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
        JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY,
    }
)


__all__ = ["insufficient_scope_challenge", "is_permission_denial", "modern_error_status"]
