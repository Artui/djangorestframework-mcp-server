"""Shared helpers for the sync and async streamable-HTTP viewsets."""

from __future__ import annotations

from typing import Any

from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.constants import (
    SESSION_MISSING_HINT,
    SESSION_UNKNOWN_HINT,
    JsonRpcErrorCode,
)
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


def is_permission_denial(result: Any) -> bool:
    """Whether a dispatch result is a permission denial needing HTTP 403.

    The MCP authorization spec's error table is normative: ``401`` for
    "authorization required or token invalid", ``403`` for "invalid scopes or
    insufficient permissions". A denial is therefore not a plain JSON-RPC error
    riding inside a ``200`` — the status carries meaning a client acts on.
    """
    return isinstance(result, JsonRpcError) and result.code == JsonRpcErrorCode.FORBIDDEN


def insufficient_scope_challenge(result: JsonRpcError, backend: MCPAuthBackend) -> str:
    """Build the ``WWW-Authenticate`` challenge for a permission denial.

    RFC 6750 §3.1 defines ``insufficient_scope`` plus a ``scope`` parameter
    listing what would satisfy the request, which is how a client knows what to
    ask for on the next authorization round.

    A denial with no scopes attached — a non-scope permission such as
    ``DjangoPermRequired`` — still gets a 403, but no ``error=`` / ``scope=``:
    RFC 6750 defines neither for that case, and inventing a scope the client
    cannot obtain would send it round a pointless loop.
    """
    data: Any = result.data if isinstance(result.data, dict) else {}
    scopes: list[str] | None = data.get("requiredScopes") or None
    return backend.www_authenticate_challenge(
        scopes=scopes, error="insufficient_scope" if scopes else None
    )


def modern_error_status(error: JsonRpcError) -> int:
    """HTTP status for a JSON-RPC error on the modern transport.

    The modern revision makes several statuses normative, each carrying
    information the JSON-RPC code alone does not:

    - ``404`` for an unknown method, which is what lets a client tell a modern
      MCP endpoint from a legacy HTTP+SSE server that hosts none: both answer
      ``404``, but only ours carries a ``-32601`` body, and the spec's fallback
      algorithm reads exactly that.
    - ``400`` for the three spec-reserved rejections (header mismatch,
      unsupported version, missing client capability). A client inspects the
      body before falling back to ``initialize``: a recognised modern error
      means "fix the request", anything else means "this server is legacy".
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


def session_gate_failure(
    session_id: str | None, *, owner_matches: bool
) -> tuple[str, int, str] | None:
    """Decide the legacy session gate's outcome. ``None`` means the request passes.

    Returns ``(message, status, hint)``, shared by the sync and async viewsets
    so the two cannot drift. ``hint`` is the ``MCP-Error`` slug.

    **The two statuses are not interchangeable.** A request *without* an
    ``Mcp-Session-Id`` header "SHOULD" get ``400``, while ``404`` is specified
    for a request *"containing that session ID"* after the server has dropped
    it — and ``2025-11-25`` lists ``400`` among the statuses that send a client
    down the legacy-fallback path, so the wrong code lands it in the wrong
    branch. Splitting them leaks nothing: the caller already knows whether it
    sent a header.

    The pair that must stay **merged** is *unknown id* versus *id owned by
    another principal*: those are facts about someone else's session, so both
    render ``404`` with one message and the gate is not an ownership oracle.
    """
    if not session_id:
        return ("Missing MCP-Session-Id", 400, SESSION_MISSING_HINT)
    if not owner_matches:
        return ("Unknown or invalid MCP-Session-Id", 404, SESSION_UNKNOWN_HINT)
    return None


__all__ = [
    "insufficient_scope_challenge",
    "is_permission_denial",
    "modern_error_status",
    "session_gate_failure",
]
