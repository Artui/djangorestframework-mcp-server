from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.http import HttpRequest

from rest_framework_mcp.auth.token_info import TokenInfo

# Sentinel indicating the limiter should derive a per-token key. Custom
# ``key`` callables can produce any string — common patterns are per-user,
# per-IP, or per-binding-plus-user.
_DEFAULT_KEY_PREFIX: str = "drf-mcp:rl"


def _default_key(request: HttpRequest, token: TokenInfo) -> str:
    """Per-user key when authenticated, falling back to remote-addr otherwise."""
    user_id: Any = getattr(token.user, "id", None)
    if user_id is not None:
        return f"u:{user_id}"
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


class FixedWindowRateLimit:
    """A fixed-window-counter rate limiter backed by ``django.core.cache``.

    The window is bucketed by absolute time: each integer multiple of
    ``per_seconds`` since the epoch starts a new counter. Simple and
    sufficient for protecting against runaway clients; not as smooth as a
    sliding window but doesn't require a sorted set.

    The cache key is namespaced with ``namespace`` so multiple limits on the
    same binding (e.g. burst + steady-state) don't share counters. ``key``
    customises the bucket dimension — defaults to per-token-user.

    The cache **must** be a shared backend in multi-process deployments;
    Django's ``locmem`` cache is fine for tests but won't enforce a global
    limit across worker processes.
    """

    def __init__(
        self,
        *,
        max_calls: int,
        per_seconds: int,
        namespace: str = "default",
        key: Callable[[HttpRequest, TokenInfo], str] | None = None,
    ) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        if per_seconds <= 0:
            raise ValueError("per_seconds must be positive")
        self._max: int = max_calls
        self._window: int = per_seconds
        self._namespace: str = namespace
        self._key_fn: Callable[[HttpRequest, TokenInfo], str] = key or _default_key

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        now: int = int(time.time())
        bucket: int = now // self._window
        cache_key: str = (
            f"{_DEFAULT_KEY_PREFIX}:{self._namespace}:{self._key_fn(request, token)}:{bucket}"
        )
        # ``add`` initialises the counter to 1 atomically when absent; ``incr``
        # bumps it. Together they're the standard fixed-window primitive that
        # works on Memcached, Redis, and Django's locmem cache.
        if cache.add(cache_key, 1, timeout=self._window):
            return None
        try:
            count: int = cache.incr(cache_key)
        except ValueError:  # pragma: no cover - cache evicted between add and incr
            cache.add(cache_key, 1, timeout=self._window)
            return None
        if count <= self._max:
            return None
        # Window resets at the next bucket boundary.
        retry_after: int = (bucket + 1) * self._window - now
        return max(retry_after, 1)


__all__ = ["FixedWindowRateLimit"]
