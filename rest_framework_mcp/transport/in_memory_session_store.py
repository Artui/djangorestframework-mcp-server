from __future__ import annotations

import secrets


class InMemorySessionStore:
    """Process-local session store. Useful for tests and single-process dev servers.

    State lives on the instance, so each store is isolated. Multi-process
    deployments should use :class:`DjangoCacheSessionStore` instead — this
    class will not see sessions created in another process.
    """

    def __init__(self) -> None:
        self._sessions: set[str] = set()

    def create(self) -> str:
        token: str = secrets.token_urlsafe(24)
        self._sessions.add(token)
        return token

    def exists(self, session_id: str) -> bool:
        return session_id in self._sessions

    def destroy(self, session_id: str) -> None:
        self._sessions.discard(session_id)


__all__ = ["InMemorySessionStore"]
