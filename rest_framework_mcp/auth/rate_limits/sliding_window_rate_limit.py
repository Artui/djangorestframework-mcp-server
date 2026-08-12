from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.http import HttpRequest

from rest_framework_mcp.auth.types.token_info import TokenInfo

# Distinct from the other limiters' prefixes so an operator can tell which
# scheme produced a counter from the cache key alone.
_DEFAULT_KEY_PREFIX: str = "drf-mcp:rl-sw"


def _default_key(request: HttpRequest, token: TokenInfo) -> str:
    """Per-user when authenticated, ``REMOTE_ADDR`` otherwise."""
    user_id: Any = getattr(token.user, "id", None)
    if user_id is not None:
        return f"u:{user_id}"
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


class SlidingWindowRateLimit:
    """Sliding-window rate limiter using a list of timestamps in cache.

    Avoids the fixed-window edge case where a client issues ``2 * max_calls``
    requests across two adjacent windows: the timestamps of recent calls are
    stored in a cache entry, and each call prunes the expired ones and compares
    the live count against ``max_calls``.

    Trade-offs against :class:`FixedWindowRateLimit`:

    - **Smoother**: limits the actual rate over the trailing ``per_seconds``,
      not bucketed counts.
    - **Memory cost**: up to ``max_calls`` timestamps per key.
    - **Read-modify-write**: no atomic guarantee, unlike the fixed window's
      ``cache.add`` + ``cache.incr``. Concurrent calls can read stale state and
      admit a few extra requests under contention; for strict atomicity in a
      multi-worker deployment, back the limiter with a Redis-Lua script.

    The cache **must** be a shared backend in multi-process deployments;
    Django's ``locmem`` works for tests but shares no state across workers.
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
        timestamps: list[float] = cache.get(cache_key) or []
        live: list[float] = [ts for ts in timestamps if ts > cutoff]
        if len(live) >= self._max:
            # The oldest live timestamp is when the window regains capacity;
            # clamped to >= 1 so ``Retry-After`` is always usable.
            retry_at: float = live[0] + self._window
            retry_after: int = max(int(retry_at - now), 1)
            # Persist the pruned list so denied calls stop re-walking expired
            # entries.
            cache.set(cache_key, live, timeout=self._window)
            return retry_after
        live.append(now)
        cache.set(cache_key, live, timeout=self._window)
        return None


__all__ = ["SlidingWindowRateLimit"]
