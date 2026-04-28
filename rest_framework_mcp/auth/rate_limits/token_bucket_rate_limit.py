from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.http import HttpRequest

from rest_framework_mcp.auth.token_info import TokenInfo

# Token-bucket key namespace. Distinct from the fixed/sliding-window prefixes
# so operators can spot the scheme at a glance in cache keyspaces.
_DEFAULT_KEY_PREFIX: str = "drf-mcp:rl-tb"


def _default_key(request: HttpRequest, token: TokenInfo) -> str:
    """Per-user when authenticated, ``REMOTE_ADDR`` otherwise."""
    user_id: Any = getattr(token.user, "id", None)
    if user_id is not None:
        return f"u:{user_id}"
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


class TokenBucketRateLimit:
    """Token-bucket rate limiter using Django cache for state.

    A bucket has at most ``capacity`` tokens. Each accepted call consumes
    one token; the bucket refills continuously at ``refill_per_second``
    tokens per second. When empty, the limiter returns the suggested
    retry-after time until at least one token is available again.

    Trade-offs vs the sliding-window scheme:

    - **Burst-friendly**: a full bucket can absorb a burst of ``capacity``
      requests instantly, then rate-limit the steady state at
      ``refill_per_second``. Useful when consumers naturally batch.
    - **Read-modify-write**: like the sliding-window class, this is not
      strictly atomic across concurrent workers. Under contention a small
      number of extra tokens may slip through. For strict atomicity in
      multi-worker deployments, back the limiter with a Redis-Lua script.

    The cache **must** be a shared backend (Memcached or Redis) in
    multi-process deployments; Django's ``locmem`` is fine for tests but
    won't share state across workers.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        namespace: str = "default",
        key: Callable[[HttpRequest, TokenInfo], str] | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be positive")
        self._capacity: int = capacity
        self._refill: float = float(refill_per_second)
        self._namespace: str = namespace
        self._key_fn: Callable[[HttpRequest, TokenInfo], str] = key or _default_key
        # Cache TTL — we let entries expire after a few drain-times so dead
        # buckets don't accumulate forever. Worst case the bucket is
        # re-created at full capacity, which is the safe direction.
        self._ttl: int = max(int(self._capacity / self._refill * 2), 60)

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        now: float = time.time()
        cache_key: str = f"{_DEFAULT_KEY_PREFIX}:{self._namespace}:{self._key_fn(request, token)}"
        # Stored shape: (tokens, last_refill_ts). Missing key → bucket is full.
        state: tuple[float, float] | None = cache.get(cache_key)
        if state is None:
            tokens: float = float(self._capacity)
            last: float = now
        else:
            stored_tokens, last = state
            elapsed: float = max(now - last, 0.0)
            tokens = min(self._capacity, stored_tokens + elapsed * self._refill)

        if tokens >= 1.0:
            cache.set(cache_key, (tokens - 1.0, now), timeout=self._ttl)
            return None

        # Time until the bucket has one token: how long does refill at
        # ``refill_per_second`` take to top up the deficit. Clamp to >= 1
        # so callers always see a usable ``Retry-After`` value.
        deficit: float = 1.0 - tokens
        retry_after: int = max(math.ceil(deficit / self._refill), 1)
        # Persist the (still-empty) state so concurrent requests see the same
        # "denied" snapshot until the window slides.
        cache.set(cache_key, (tokens, now), timeout=self._ttl)
        return retry_after


__all__ = ["TokenBucketRateLimit"]
