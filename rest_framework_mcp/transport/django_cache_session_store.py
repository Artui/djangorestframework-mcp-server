from __future__ import annotations

import hashlib
import secrets

from django.core.cache import cache

_KEY_PREFIX: str = "drf-mcp:session:"
_DEFAULT_TTL_SECONDS: int = 60 * 60 * 24  # 24h — long enough for typical client sessions
# Enough to make a collision between two servers in one process implausible,
# while keeping the key readable. Not a security boundary — the namespace is a
# partition, not a secret.
_NAMESPACE_DIGEST_CHARS: int = 12


class DjangoCacheSessionStore:
    """Session store backed by ``django.core.cache``.

    Works across processes — the production-suitable default. TTL is fixed
    at 24 hours; for stricter pinning, projects can subclass and override
    :meth:`create`.

    The cached value is the owning principal id, so :meth:`owner` is a
    single cache read. Sessions written by pre-0.7 versions stored ``True``
    instead of a principal — those fail the ownership comparison and the
    client transparently re-initializes.

    **Namespacing.** Every instance built by :class:`MCPServer` keys its cache
    entries under the server's ``name`` — the spec's programmatic identifier —
    so two servers in one project cannot see each other's sessions. Without it
    they share one flat key space over the same Django cache: a session minted
    at one satisfies the other's ownership check, and a ``DELETE`` against
    either destroys the other's session.

    The namespace is **hashed** into the key rather than interpolated raw:
    ``name`` is consumer-supplied and free-form (``"My Invoicing Server"``),
    while cache keys must survive backends like memcached that reject spaces
    and control characters and cap length at 250. Keys are therefore
    ``drf-mcp:session:<digest>:<token>``.

    Constructing the store yourself opts out of that — you own the namespace::

        MCPServer(session_store=DjangoCacheSessionStore(namespace="internal"))

    so two hand-built stores with no ``namespace`` collide exactly as before.
    """

    def __init__(self, *, namespace: str | None = None) -> None:
        # Folded into the prefix at construction rather than per key: the
        # namespace is fixed for the store's lifetime, so there's nothing to
        # recompute on the read path.
        self._prefix: str = (
            _KEY_PREFIX if namespace is None else f"{_KEY_PREFIX}{_digest(namespace)}:"
        )

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    def create(self, *, principal_id: str) -> str:
        token: str = secrets.token_urlsafe(24)
        cache.set(self._key(token), principal_id, timeout=_DEFAULT_TTL_SECONDS)
        return token

    def exists(self, session_id: str) -> bool:
        return cache.get(self._key(session_id)) is not None

    def owner(self, session_id: str) -> str | None:
        value = cache.get(self._key(session_id))
        return value if isinstance(value, str) else None

    def destroy(self, session_id: str) -> None:
        cache.delete(self._key(session_id))


def _digest(namespace: str) -> str:
    """Reduce a free-form namespace to a cache-key-safe token.

    Not for secrecy — a server name is public. This only guarantees the key is
    well-formed on every cache backend, whatever the consumer named the server.
    """
    return hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:_NAMESPACE_DIGEST_CHARS]


__all__ = ["DjangoCacheSessionStore"]
