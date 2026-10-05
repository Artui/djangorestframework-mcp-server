from __future__ import annotations

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from rest_framework_mcp.transport.redis_sse_replay_buffer import RedisSSEReplayBuffer
from rest_framework_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer


def _client() -> FakeAsyncRedis:
    """Fresh ``fakeredis`` client per test.

    The explicit ``FakeServer`` is load-bearing, not ceremony: before
    fakeredis 2.21 a bare ``FakeAsyncRedis()`` shares one process-wide
    server, so keys survive between tests and an assertion about a key
    that should not exist reads whatever the previous test left. Our
    declared floor is older than that, so this held on the ceiling and
    not at the floor.
    """
    return FakeAsyncRedis(server=FakeServer())


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


async def test_every_write_asks_redis_to_trim_approximately_at_max_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound is Redis's to keep, so what the buffer owns is the request:
    ``MAXLEN ~ max_events`` on every ``XADD``.

    The test below cannot hold the ``~``, because exact trimming satisfies both
    of its bounds. This one does: dropping ``approximate=True`` would make the
    retained length tidier and make Redis cut inside a node on every write,
    which is the cost ``~`` exists to avoid.
    """
    client = _client()
    xadd = client.xadd
    requested: list[tuple[object, object]] = []

    async def spy(*args: object, **kwargs: object) -> object:
        requested.append((kwargs.get("maxlen"), kwargs.get("approximate")))
        return await xadd(*args, **kwargs)

    monkeypatch.setattr(client, "xadd", spy)
    buf = RedisSSEReplayBuffer(client, max_events=7)
    await buf.record("s", {"n": 1})
    await buf.record("s", {"n": 2})
    assert requested == [(7, True), (7, True)]
    await client.aclose()


async def test_trimming_keeps_the_newest_max_events_and_bounds_the_rest() -> None:
    """``MAXLEN ~ N`` trims only whole internal nodes of the stream, so Redis
    keeps at least the newest N events and up to one node's worth more
    (``stream-node-max-entries``, 100 by default).

    Both bounds are asserted without assuming a node size, by writing until
    Redis first trims. That matters because the fake's answer moved under
    this test: fakeredis before 2.39 trimmed ``~`` exactly, which Redis never
    does, and from 2.39 it drops whole nodes as Redis does. A fixed write
    count either never crosses a node boundary, and so asserts nothing about
    trimming, or bakes in the node size.
    """
    client = _client()
    buf = RedisSSEReplayBuffer(client, max_events=2)
    key = "drf-mcp:sse-replay:s"
    recorded: list[tuple[str, object]] = []
    # The cap turns a stream that is never trimmed into a failure rather than
    # a hang; Redis's default node is a tenth of it.
    for n in range(1000):
        recorded.append((await buf.record("s", {"n": n}), {"n": n}))
        if await client.xlen(key) < len(recorded):
            break
    else:
        pytest.fail("1000 writes against max_events=2 and the stream was never trimmed")
    trimmed_at = len(recorded)

    # The lower bound is the one a reconnecting client relies on: every event
    # inside the window is still there, and what went, went oldest first.
    out = await _drain(buf.replay("s", "0-0"))
    assert len(out) >= 2
    assert out == recorded[-len(out) :]

    # The upper bound: the stream never grows back to the length that made
    # Redis trim it, so the margin over max_events is one node and no more.
    for n in range(trimmed_at):
        await buf.record("s", {"n": n})
        assert await client.xlen(key) < trimmed_at
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


async def test_a_recorded_stream_carries_an_expiry() -> None:
    """``forget`` runs only on an explicit ``DELETE``, and sessions ordinarily
    end by expiring or by a client dropping the connection — so without a TTL
    every such session leaves its stream in Redis for good."""
    client = _client()
    buf = RedisSSEReplayBuffer(client, ttl_seconds=120)
    await buf.record("s", {"n": 1})
    ttl = await client.ttl("drf-mcp:sse-replay:s")
    assert 0 < ttl <= 120
    await client.aclose()


async def test_the_expiry_is_renewed_by_every_write() -> None:
    """An active stream must not expire under a client that is still there."""
    client = _client()
    buf = RedisSSEReplayBuffer(client, ttl_seconds=120)
    await buf.record("s", {"n": 1})
    await client.expire("drf-mcp:sse-replay:s", 5)
    await buf.record("s", {"n": 2})
    assert await client.ttl("drf-mcp:sse-replay:s") > 5
    await client.aclose()


async def test_the_expiry_can_be_disabled() -> None:
    client = _client()
    buf = RedisSSEReplayBuffer(client, ttl_seconds=None)
    await buf.record("s", {"n": 1})
    # ``-1`` is Redis for "the key exists and never expires".
    assert await client.ttl("drf-mcp:sse-replay:s") == -1
    await client.aclose()


def test_an_unusable_expiry_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        RedisSSEReplayBuffer(client=_client(), ttl_seconds=0)


async def test_two_servers_can_keep_separate_key_spaces() -> None:
    """The cache-backed stores fold the server's ``name`` into their key prefix
    so two servers in one project cannot read each other's state. A Redis
    client is the consumer's to construct, so here the namespace is an argument
    — but the property it buys is the same one."""
    client = _client()
    public = RedisSSEReplayBuffer(client, namespace="public")
    internal = RedisSSEReplayBuffer(client, namespace="internal")
    recorded = await public.record("shared-id", {"n": 1})
    assert await _drain(internal.replay("shared-id", "0-0")) == []
    assert await _drain(public.replay("shared-id", "0-0")) == [(recorded, {"n": 1})]
    await client.aclose()
