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

    The cached value is the owning principal id, so :meth:`owner` is a
    single cache read. Sessions written by pre-0.7 versions stored ``True``
    instead of a principal — those fail the ownership comparison and the
    client transparently re-initializes.
    """

    def create(self, *, principal_id: str) -> str:
        token: str = secrets.token_urlsafe(24)
        cache.set(_key(token), principal_id, timeout=_DEFAULT_TTL_SECONDS)
        return token

    def exists(self, session_id: str) -> bool:
        return cache.get(_key(session_id)) is not None

    def owner(self, session_id: str) -> str | None:
        value = cache.get(_key(session_id))
        return value if isinstance(value, str) else None

    def destroy(self, session_id: str) -> None:
        cache.delete(_key(session_id))


__all__ = ["DjangoCacheSessionStore"]
