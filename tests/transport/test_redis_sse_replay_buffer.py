from __future__ import annotations

import pytest
from fakeredis import FakeAsyncRedis

from rest_framework_mcp.transport.redis_sse_replay_buffer import RedisSSEReplayBuffer
from rest_framework_mcp.transport.sse_replay_buffer import SSEReplayBuffer


def _client() -> FakeAsyncRedis:
    """Fresh ``fakeredis`` client per test."""
    return FakeAsyncRedis()


async def _drain(it) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    async for pair in it:
        out.append(pair)
    return out


async def test_record_returns_monotonic_ids() -> None:
    client = _client()
    buf = RedisSSEReplayBuffer(client)
    a = await buf.record("s", {"n": 1})
    b = await buf.record("s", {"n": 2})
    # Stream IDs are ``ms-seq`` strings; lexicographic order matches recency
    # because Redis pads the seq monotonically.
    assert a < b
    await client.aclose()


async def test_replay_yields_events_after_id() -> None:
    client = _client()
    buf = RedisSSEReplayBuffer(client)
    first = await buf.record("s", {"n": 1})
    second = await buf.record("s", {"n": 2})
    third = await buf.record("s", {"n": 3})
    out = await _drain(buf.replay("s", first))
    assert out == [(second, {"n": 2}), (third, {"n": 3})]
    await client.aclose()


async def test_replay_with_none_yields_nothing() -> None:
    client = _client()
    buf = RedisSSEReplayBuffer(client)
    await buf.record("s", {"n": 1})
    assert await _drain(buf.replay("s", None)) == []
    await client.aclose()


async def test_replay_unknown_session_yields_nothing() -> None:
    client = _client()
    buf = RedisSSEReplayBuffer(client)
    assert await _drain(buf.replay("never-existed", "0-0")) == []
    await client.aclose()


async def test_eviction_at_max_events() -> None:
    """``MAXLEN ~ N`` keeps roughly the last N events. ``approximate=True``
    means Redis trims at internal node boundaries, so we just assert that
    very-old events drop and the most recent remain.
    """
    client = _client()
    buf = RedisSSEReplayBuffer(client, max_events=2)
    for n in range(20):
        await buf.record("s", {"n": n})
    out = await _drain(buf.replay("s", "0-0"))
    # At most a handful retained; the last 2 are guaranteed-present.
    assert len(out) <= 5
    payloads = [pair[1] for pair in out]
    assert {"n": 18} in payloads
    assert {"n": 19} in payloads
    await client.aclose()


async def test_buckets_are_per_session() -> None:
    client = _client()
    buf = RedisSSEReplayBuffer(client)
    await buf.record("a", {"src": "a"})
    await buf.record("b", {"src": "b"})
    out_a = await _drain(buf.replay("a", "0-0"))
    out_b = await _drain(buf.replay("b", "0-0"))
    assert [p[1] for p in out_a] == [{"src": "a"}]
    assert [p[1] for p in out_b] == [{"src": "b"}]
    await client.aclose()


async def test_forget_drops_session_state() -> None:
    client = _client()
    buf = RedisSSEReplayBuffer(client)
    await buf.record("s", {"n": 1})
    await buf.forget("s")
    assert await _drain(buf.replay("s", "0-0")) == []
    await client.aclose()


async def test_satisfies_protocol() -> None:
    client = _client()
    buf = RedisSSEReplayBuffer(client)
    assert isinstance(buf, SSEReplayBuffer)
    await client.aclose()


def test_invalid_max_events_rejected() -> None:
    with pytest.raises(ValueError, match="max_events"):
        RedisSSEReplayBuffer(client=_client(), max_events=0)


async def test_import_error_when_redis_absent(monkeypatch) -> None:
    """The constructor surfaces a clear error when ``redis`` isn't installed."""
    import rest_framework_mcp.transport.redis_sse_replay_buffer as mod

    monkeypatch.setattr(mod, "AsyncRedis", None)
    with pytest.raises(ImportError, match="djangorestframework-mcp-server\\[redis\\]"):
        RedisSSEReplayBuffer(client=object())
