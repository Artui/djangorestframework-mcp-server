from __future__ import annotations

import time

import pytest
from django.core.cache import cache
from django.http import HttpRequest

from rest_framework_mcp.auth.rate_limits.token_bucket_rate_limit import (
    TokenBucketRateLimit,
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


def test_full_bucket_absorbs_burst() -> None:
    """A fresh bucket starts full and accepts ``capacity`` calls instantly."""
    limiter = TokenBucketRateLimit(capacity=3, refill_per_second=1.0)
    token = TokenInfo(user=_User(1))
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is None


def test_empty_bucket_returns_retry_after() -> None:
    limiter = TokenBucketRateLimit(capacity=2, refill_per_second=1.0)
    token = TokenInfo(user=_User(1))
    limiter.consume(_request(), token)
    limiter.consume(_request(), token)
    retry = limiter.consume(_request(), token)
    assert isinstance(retry, int)
    assert retry >= 1


def test_refills_with_real_time() -> None:
    """After draining the bucket, waiting long enough refills at least one token."""
    limiter = TokenBucketRateLimit(capacity=1, refill_per_second=10.0)
    token = TokenInfo(user=_User(1))
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is not None  # drained
    time.sleep(0.2)  # 0.2s * 10/s = 2 tokens worth (capped at 1)
    assert limiter.consume(_request(), token) is None


def test_capacity_caps_refill() -> None:
    """A long pause must not over-fill beyond ``capacity``."""
    limiter = TokenBucketRateLimit(capacity=2, refill_per_second=100.0)
    token = TokenInfo(user=_User(1))
    # Drain.
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is None
    # Sleep way longer than needed to refill.
    time.sleep(0.2)  # would refill 20 tokens unbounded
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is None
    # Still capped — third call is denied.
    assert limiter.consume(_request(), token) is not None


def test_buckets_are_per_user() -> None:
    limiter = TokenBucketRateLimit(capacity=1, refill_per_second=0.1)
    a = TokenInfo(user=_User(1))
    b = TokenInfo(user=_User(2))
    assert limiter.consume(_request(), a) is None
    assert limiter.consume(_request(), b) is None
    assert limiter.consume(_request(), a) is not None
    assert limiter.consume(_request(), b) is not None


def test_anonymous_falls_back_to_remote_addr() -> None:
    limiter = TokenBucketRateLimit(capacity=1, refill_per_second=0.1)
    token = TokenInfo(user=None)
    assert limiter.consume(_request(), token) is None
    assert limiter.consume(_request(), token) is not None


def test_anonymous_unknown_remote_addr() -> None:
    """Missing ``REMOTE_ADDR`` falls back to the literal ``unknown`` bucket."""
    limiter = TokenBucketRateLimit(capacity=1, refill_per_second=0.1)
    token = TokenInfo(user=None)
    req = HttpRequest()  # no REMOTE_ADDR
    assert limiter.consume(req, token) is None
    assert limiter.consume(req, token) is not None


def test_custom_key_callable() -> None:
    def static(request: HttpRequest, token: TokenInfo) -> str:
        return "shared"

    limiter = TokenBucketRateLimit(capacity=1, refill_per_second=0.1, key=static)
    assert limiter.consume(_request(), TokenInfo(user=_User(1))) is None
    # Different token but the custom key collapses them into one bucket.
    assert limiter.consume(_request(), TokenInfo(user=_User(2))) is not None


def test_namespace_isolates_buckets() -> None:
    a = TokenBucketRateLimit(capacity=1, refill_per_second=0.1, namespace="burst")
    b = TokenBucketRateLimit(capacity=1, refill_per_second=0.1, namespace="steady")
    token = TokenInfo(user=_User(1))
    assert a.consume(_request(), token) is None
    assert b.consume(_request(), token) is None  # different namespace, separate bucket


def test_invalid_capacity_rejected() -> None:
    with pytest.raises(ValueError, match="capacity"):
        TokenBucketRateLimit(capacity=0, refill_per_second=1.0)


def test_invalid_refill_rejected() -> None:
    with pytest.raises(ValueError, match="refill_per_second"):
        TokenBucketRateLimit(capacity=1, refill_per_second=0.0)


def test_satisfies_mcprate_limit_protocol() -> None:
    from rest_framework_mcp.auth.rate_limits.mcp_rate_limit import MCPRateLimit

    limiter = TokenBucketRateLimit(capacity=1, refill_per_second=1.0)
    assert isinstance(limiter, MCPRateLimit)
