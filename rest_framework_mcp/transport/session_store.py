from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Pluggable persistence for ``MCP-Session-Id`` lifecycle.

    The transport calls :meth:`create` after a successful ``initialize`` and
    :meth:`exists` on every subsequent request to enforce that clients
    re-initialize after a server restart. :meth:`destroy` is invoked on
    HTTP DELETE.

    Stores need not retain rich state today (the MCP spec only demands the
    ID is recognised), but the interface leaves room for future extension
    such as protocol-version pinning per session.
    """

    def create(self) -> str: ...

    def exists(self, session_id: str) -> bool: ...

    def destroy(self, session_id: str) -> None: ...


__all__ = ["SessionStore"]
