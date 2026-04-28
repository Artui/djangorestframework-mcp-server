from __future__ import annotations

import secrets

from django.core.cache import cache

_KEY_PREFIX: str = "drf-mcp:session:"
_DEFAULT_TTL_SECONDS: int = 60 * 60 * 24  # 24h — long enough for typical client sessions


def _key(session_id: str) -> str:
    return f"{_KEY_PREFIX}{session_id}"


class DjangoCacheSessionStore:
    """Session store backed by ``django.core.cache``.

    Works across processes — the production-suitable default. TTL is fixed
    at 24 hours; for stricter pinning, projects can subclass and override
    :meth:`create`.
    """

    def create(self) -> str:
        token: str = secrets.token_urlsafe(24)
        cache.set(_key(token), True, timeout=_DEFAULT_TTL_SECONDS)
        return token

    def exists(self, session_id: str) -> bool:
        return cache.get(_key(session_id)) is not None

    def destroy(self, session_id: str) -> None:
        cache.delete(_key(session_id))


__all__ = ["DjangoCacheSessionStore"]
