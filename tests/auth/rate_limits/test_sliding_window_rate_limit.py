from __future__ import annotations

import time

import pytest
from django.core.cache import cache
from django.http import HttpRequest

from rest_framework_mcp.auth.rate_limits.sliding_window_rate_limit import (
    SlidingWindowRateLimit,
)
from rest_framework_mcp.auth.token_info import TokenInfo


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class _User:
    def __init__(self, uid: int) -> None:
        self.id = uid


def _request() -> HttpRequest:
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "203.0.113.7"
    return req


def test_allow_under_limit() -> None:
    limiter = SlidingWindowRateLimit(max_calls=3, per_seconds=60)
    token = TokenInfo(user=_User(1))
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is None


def test_deny_over_limit_returns_retry_after() -> None:
    limiter = SlidingWindowRateLimit(max_calls=2, per_seconds=60)
    token = TokenInfo(user=_User(1))
    limiter.consume(_request(), token)
    limiter.consume(_request(), token)
    retry = limiter.consume(_request(), token)
    assert isinstance(retry, int)
    assert retry > 0


def test_window_slides_with_real_time() -> None:
    """Pruning expires old timestamps so capacity returns smoothly."""
    limiter = SlidingWindowRateLimit(max_calls=2, per_seconds=1)
    token = TokenInfo(user=_User(1))
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is not None  # at the cap
    # Wait for the window to slide past the first two timestamps.
    time.sleep(1.1)
    assert limiter.consume(_request(), token) is None


def test_buckets_are_per_user() -> None:
    limiter = SlidingWindowRateLimit(max_calls=1, per_seconds=60)
    a = TokenInfo(user=_User(1))
    b = TokenInfo(user=_User(2))
    assert limiter.consume(_request(), a) is None
    assert limiter.consume(_request(), b) is None
    assert limiter.consume(_request(), a) is not None
    assert limiter.consume(_request(), b) is not None


def test_anonymous_falls_back_to_remote_addr() -> None:
    limiter = SlidingWindowRateLimit(max_calls=1, per_seconds=60)
    token = TokenInfo(user=None)
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is not None


def test_custom_key_callable() -> None:
    def static(request: HttpRequest, token: TokenInfo) -> str:
        return "shared"

    limiter = SlidingWindowRateLimit(max_calls=1, per_seconds=60, key=static)
    assert limiter.consume(_request(), TokenInfo(user=_User(1))) is None
    # Second token but same key → same bucket → denied.
    assert limiter.consume(_request(), TokenInfo(user=_User(2))) is not None


def test_namespace_isolates_counters() -> None:
    a = SlidingWindowRateLimit(max_calls=1, per_seconds=60, namespace="burst")
    b = SlidingWindowRateLimit(max_calls=1, per_seconds=60, namespace="steady")
    token = TokenInfo(user=_User(1))
    assert a.consume(_request(), token) is None
    assert b.consume(_request(), token) is None  # different namespace, separate bucket


def test_invalid_max_calls_rejected() -> None:
    with pytest.raises(ValueError, match="max_calls"):
        SlidingWindowRateLimit(max_calls=0, per_seconds=60)


def test_invalid_per_seconds_rejected() -> None:
    with pytest.raises(ValueError, match="per_seconds"):
        SlidingWindowRateLimit(max_calls=1, per_seconds=0)


def test_satisfies_mcprate_limit_protocol() -> None:
    from rest_framework_mcp.auth.rate_limits.mcp_rate_limit import MCPRateLimit

    limiter = SlidingWindowRateLimit(max_calls=1, per_seconds=60)
    assert isinstance(limiter, MCPRateLimit)
