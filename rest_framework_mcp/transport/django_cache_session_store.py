from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from django.core.cache import cache

from rest_framework_mcp.conf import get_setting


@dataclass(frozen=True)
class _Entry:
    """What a session key holds: the owning principal, and when it was minted.

    ``created_at`` exists solely to enforce the absolute cap — without it a
    sliding window has no anchor and cannot expire a continuously-used session.
    """

    principal_id: str
    created_at: float


_KEY_PREFIX: str = "drf-mcp:session:"
# The default expiry windows live in ``conf.DEFAULTS``, deliberately not here:
# a module-private constant is exactly what made them unreachable without
# subclassing the store.
#
# Enough to make a collision between two servers in one process implausible,
# while keeping the key readable. Not a security boundary — the namespace is a
# partition, not a secret.
_NAMESPACE_DIGEST_CHARS: int = 12


class DjangoCacheSessionStore:
    """Session store backed by ``django.core.cache``.

    Works across processes — the production-suitable default.

    **Two windows, both configurable** (``SESSION_TTL_SECONDS`` /
    ``SESSION_MAX_AGE_SECONDS``, or the constructor arguments here). The TTL is
    an **idle** window that restarts on every successful :meth:`owner` read, so
    a session in continuous use never lapses; the max age is the **absolute**
    ceiling that stops a sliding window outliving a revoked principal. Both were
    once a single fixed 24-hour constant, which meant an actively-used connector
    still died on the mark.

    ⚠ **Neither window can promise more than the cache underneath.** An
    eviction policy like Redis's ``allkeys-lru`` drops session keys well before
    any timeout, and the client cannot tell that apart from expiry. If sessions
    vanish early, check the eviction policy before these settings.

    The cached value carries the owning principal and the mint time, so
    :meth:`owner` is a single read. Sessions written by pre-0.7 versions stored
    ``True`` instead of a principal — those fail the ownership comparison and
    the client transparently re-initializes. A bare principal string, the shape
    written up to 0.24, is honoured and rewritten in the current shape so an
    upgrade doesn't log every current holder out.

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

    def __init__(
        self,
        *,
        namespace: str | None = None,
        ttl_seconds: int | None = None,
        max_age_seconds: int | None = None,
    ) -> None:
        # Folded into the prefix at construction rather than per key: the
        # namespace is fixed for the store's lifetime, so there's nothing to
        # recompute on the read path.
        self._prefix: str = (
            _KEY_PREFIX if namespace is None else f"{_KEY_PREFIX}{_digest(namespace)}:"
        )
        self._ttl_seconds: int = (
            ttl_seconds if ttl_seconds is not None else get_setting("SESSION_TTL_SECONDS")
        )
        self._max_age_seconds: int | None = (
            max_age_seconds
            if max_age_seconds is not None
            else get_setting("SESSION_MAX_AGE_SECONDS")
        )

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    def create(self, *, principal_id: str) -> str:
        token: str = secrets.token_urlsafe(24)
        cache.set(
            self._key(token),
            _Entry(principal_id=principal_id, created_at=time.time()),
            timeout=self._ttl_seconds,
        )
        return token

    def exists(self, session_id: str) -> bool:
        """Whether a key is present — deliberately *not* ``owner() is not None``.

        A value this store cannot read (a pre-0.7 ``True``) is present but
        ownerless, and the gate keys on :meth:`owner`, so the distinction is the
        documented one. Reading through ``owner`` here would also refresh the
        idle window, making a liveness probe extend the thing it probes.
        """
        return cache.get(self._key(session_id)) is not None

    def owner(self, session_id: str) -> str | None:
        """Resolve the owning principal, refreshing the idle window on the way.

        **Idle, not fixed.** The window restarts on every successful read, so a
        session in continuous use never lapses and only a genuinely idle one
        does. Before this, a connector talking every minute still died at the
        24-hour mark — the failure that produced this setting.

        ⚠ **Bounded by an absolute maximum age**, which is not optional. The
        principal binding is checked once, at ``initialize``; an unbounded
        sliding window would therefore keep a *revoked* principal alive for as
        long as it kept talking. This is the same argument
        ``SUBSCRIPTION_MAX_SECONDS`` already makes for subscriptions.
        """
        entry = cache.get(self._key(session_id))
        if isinstance(entry, str):
            # Written by a version that stored the bare principal. Honour it and
            # re-write it in the current shape rather than logging the holder
            # out mid-upgrade; its age is unknown, so the clock starts now.
            entry = _Entry(principal_id=entry, created_at=time.time())
        if not isinstance(entry, _Entry):
            return None
        remaining = self._remaining_lifetime(entry)
        if remaining <= 0:
            cache.delete(self._key(session_id))
            return None
        cache.set(self._key(session_id), entry, timeout=remaining)
        return entry.principal_id

    def _remaining_lifetime(self, entry: _Entry) -> int:
        """The idle window, clipped so the session cannot outlive its max age."""
        if self._max_age_seconds is None:
            return self._ttl_seconds
        left = int(entry.created_at + self._max_age_seconds - time.time())
        return min(self._ttl_seconds, left)

    def destroy(self, session_id: str) -> None:
        cache.delete(self._key(session_id))


def _digest(namespace: str) -> str:
    """Reduce a free-form namespace to a cache-key-safe token.

    Not for secrecy — a server name is public. This only guarantees the key is
    well-formed on every cache backend, whatever the consumer named the server.
    """
    return hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:_NAMESPACE_DIGEST_CHARS]


__all__ = ["DjangoCacheSessionStore"]
