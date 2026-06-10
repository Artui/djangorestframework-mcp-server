from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Pluggable persistence for ``MCP-Session-Id`` lifecycle.

    The transport calls :meth:`create` after a successful ``initialize`` —
    binding the new session to the authenticated principal — and
    :meth:`owner` on every subsequent request to enforce both that clients
    re-initialize after a server restart and that a session minted under
    one principal cannot be presented by another. :meth:`destroy` is
    invoked on HTTP DELETE (after the same ownership check).

    ``principal_id`` is an opaque string the transport derives from the
    authenticated token (see
    :func:`rest_framework_mcp.transport.utils.principal_for_token`);
    stores persist and return it verbatim.

    .. versionchanged:: 0.7
       :meth:`create` takes a required keyword-only ``principal_id`` and
       :meth:`owner` joined the protocol. Custom store implementations
       must add both; storing the principal alongside the session id is
       the only new obligation.
    """

    def create(self, *, principal_id: str) -> str: ...

    def exists(self, session_id: str) -> bool: ...

    def owner(self, session_id: str) -> str | None: ...

    def destroy(self, session_id: str) -> None: ...


__all__ = ["SessionStore"]
