from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.http import HttpRequest

from rest_framework_mcp.auth.token_info import TokenInfo

# Sliding-window key namespace. Separate from ``FixedWindowRateLimit``'s prefix
# so brokers/operators can spot which scheme produced a counter at a glance.
_DEFAULT_KEY_PREFIX: str = "drf-mcp:rl-sw"


def _default_key(request: HttpRequest, token: TokenInfo) -> str:
    """Per-user when authenticated, ``REMOTE_ADDR`` otherwise."""
    user_id: Any = getattr(token.user, "id", None)
    if user_id is not None:
        return f"u:{user_id}"
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


class SlidingWindowRateLimit:
    """Sliding-window rate limiter using a list of timestamps in cache.

    Avoids the well-known fixed-window edge case where a client can issue
    ``2 * max_calls`` requests across two adjacent windows. Stores the
    timestamps of recent calls in a Django cache entry; on each call,
    expired entries are pruned and the live count is compared against
    ``max_calls``.

    Trade-offs vs :class:`FixedWindowRateLimit`:

    - **Smoother**: limits actual request rate over the trailing
      ``per_seconds`` window, not bucketed counts.
    - **Memory cost**: the timestamp list grows with traffic up to
      ``max_calls`` entries per key; negligible for typical limits.
    - **Read-modify-write**: doesn't have the atomic guarantees of the
      fixed-window's ``cache.add`` + ``cache.incr`` primitives. Concurrent
      calls can see slightly stale state and admit a small number of
      extra requests under contention. For strict atomicity in
      multi-worker deployments, use a Redis-backed limiter with Lua.

    The cache **must** be a shared backend in multi-process deployments;
    Django's ``locmem`` works for tests but won't share state across
    workers.
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
        now: float = time.time()
        cutoff: float = now - self._window
        cache_key: str = f"{_DEFAULT_KEY_PREFIX}:{self._namespace}:{self._key_fn(request, token)}"
        # Cache stores a list of timestamps; ``None`` on first call.
        timestamps: list[float] = cache.get(cache_key) or []
        # Drop entries that fell outside the trailing window.
        live: list[float] = [ts for ts in timestamps if ts > cutoff]
        if len(live) >= self._max:
            # The oldest live timestamp determines when the window first has
            # capacity again; clamp to >= 1 so callers always see a usable
            # ``Retry-After`` value.
            retry_at: float = live[0] + self._window
            retry_after: int = max(int(retry_at - now), 1)
            # Save the pruned list so we don't keep iterating over expired
            # entries on every denied call.
            cache.set(cache_key, live, timeout=self._window)
            return retry_after
        live.append(now)
        cache.set(cache_key, live, timeout=self._window)
        return None


__all__ = ["SlidingWindowRateLimit"]
