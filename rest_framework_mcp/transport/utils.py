"""Shared helpers for the sync and async streamable-HTTP viewsets."""

from __future__ import annotations

from typing import Any

from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
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


def principal_for_token(token: TokenInfo) -> str:
    """Derive the stable principal id a session is bound to.

    Sessions minted at ``initialize`` are owned by the authenticated
    principal; every subsequent request must present the same principal or
    the session is treated as unknown (404 — deliberately indistinguishable
    from a non-existent session so ownership probing yields no oracle).

    The id is the resolved user's primary key. Unauthenticated principals
    (an ``AnonymousUser`` from a permissive backend such as
    ``AllowAnyBackend``) all map to the shared ``"anonymous"`` principal —
    session binding is only as strong as the auth backend behind it.
    """
    user: Any = token.user
    pk: Any = getattr(user, "pk", None)
    if pk is not None:
        return f"user:{pk}"
    return "anonymous"


__all__ = ["insufficient_scope_challenge", "is_permission_denial", "principal_for_token"]
