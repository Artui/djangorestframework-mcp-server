from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Pluggable persistence for ``MCP-Session-Id`` lifecycle.

    The transport calls ``create`` after a successful ``initialize``,
    binding the new session to the authenticated principal, and ``owner``
    on every subsequent request — which is what enforces both that clients
    re-initialize after a server restart and that a session minted under one
    principal cannot be presented by another. ``destroy`` runs on HTTP
    DELETE, after the same ownership check.

    ``principal_id`` is an opaque string the transport derives from the
    authenticated token (see
    ``rest_framework_mcp.auth.principal_for_token.principal_for_token``);
    stores persist and return it verbatim.
    """

    def create(self, *, principal_id: str) -> str: ...

    def exists(self, session_id: str) -> bool: ...

    def owner(self, session_id: str) -> str | None: ...

    def destroy(self, session_id: str) -> None: ...


__all__ = ["SessionStore"]
