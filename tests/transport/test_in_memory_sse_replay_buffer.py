from __future__ import annotations

import pytest

from rest_framework_mcp.transport.in_memory_sse_replay_buffer import InMemorySSEReplayBuffer
from rest_framework_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer


async def _drain(it) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    async for pair in it:
        out.append(pair)
    return out


async def test_record_returns_monotonic_ids() -> None:
    buf = InMemorySSEReplayBuffer()
    a = await buf.record("s", {"n": 1})
    b = await buf.record("s", {"n": 2})
    c = await buf.record("s", {"n": 3})
    assert a < b < c


async def test_replay_yields_events_after_id() -> None:
    buf = InMemorySSEReplayBuffer()
    first = await buf.record("s", {"n": 1})
    second = await buf.record("s", {"n": 2})
    third = await buf.record("s", {"n": 3})
    out = await _drain(buf.replay("s", first))
    assert out == [(second, {"n": 2}), (third, {"n": 3})]


async def test_replay_with_none_yields_nothing() -> None:
    buf = InMemorySSEReplayBuffer()
    await buf.record("s", {"n": 1})
    assert await _drain(buf.replay("s", None)) == []


async def test_replay_unknown_session_yields_nothing() -> None:
    buf = InMemorySSEReplayBuffer()
    assert await _drain(buf.replay("never-existed", "0000000000000001")) == []


async def test_replay_after_latest_yields_nothing() -> None:
    """Client already up to date — no events past the head."""
    buf = InMemorySSEReplayBuffer()
    last = await buf.record("s", {"n": 1})
    assert await _drain(buf.replay("s", last)) == []


async def test_eviction_at_max_events() -> None:
    """Old events drop off the back when the ring fills."""
    buf = InMemorySSEReplayBuffer(max_events=2)
    first = await buf.record("s", {"n": 1})
    await buf.record("s", {"n": 2})
    third = await buf.record("s", {"n": 3})
    # Replay from "before everything" only sees the last 2 (first was evicted).
    out = await _drain(buf.replay("s", "0000000000000000"))
    assert [pair[1] for pair in out] == [{"n": 2}, {"n": 3}]
    # Replay strictly past the first is also bounded — but here "first" was
    # evicted, so the client lost it; best-effort returns whatever is left.
    out2 = await _drain(buf.replay("s", first))
    assert [pair[1] for pair in out2] == [{"n": 2}, {"n": 3}]
    # Replay strictly past the third yields nothing.
    assert await _drain(buf.replay("s", third)) == []


async def test_buckets_are_per_session() -> None:
    buf = InMemorySSEReplayBuffer()
    a1 = await buf.record("a", {"src": "a"})
    b1 = await buf.record("b", {"src": "b"})
    # IDs are session-scoped but happen to start at 1 for each — that's fine,
    # collisions across sessions are not a problem (clients only resume
    # within their own session).
    assert a1 == b1 == "0000000000000001"
    out_a = await _drain(buf.replay("a", "0000000000000000"))
    out_b = await _drain(buf.replay("b", "0000000000000000"))
    assert out_a == [(a1, {"src": "a"})]
    assert out_b == [(b1, {"src": "b"})]


async def test_forget_drops_session_state() -> None:
    buf = InMemorySSEReplayBuffer()
    await buf.record("s", {"n": 1})
    await buf.forget("s")
    assert await _drain(buf.replay("s", "0000000000000000")) == []
    # Re-recording after forget restarts numbering at 1 — counter was cleared.
    new_id = await buf.record("s", {"n": 99})
    assert new_id == "0000000000000001"


async def test_forget_unknown_session_is_noop() -> None:
    buf = InMemorySSEReplayBuffer()
    await buf.forget("never-existed")  # must not raise


def test_invalid_max_events_rejected() -> None:
    with pytest.raises(ValueError, match="max_events"):
        InMemorySSEReplayBuffer(max_events=0)


def test_satisfies_sse_replay_buffer_protocol() -> None:
    assert isinstance(InMemorySSEReplayBuffer(), SSEReplayBuffer)


async def test_sessions_that_never_said_goodbye_are_eventually_forgotten() -> None:
    """``forget`` runs only on an explicit ``DELETE``.

    Sessions ordinarily end by expiring in the session store, which notifies
    nothing, or by a client dropping the connection — so without a bound across
    sessions every session that ever recorded an event keeps its ring and its
    counter for the life of the process, and an authenticated caller looping
    ``initialize`` plus one notify grows resident memory indefinitely.
    """
    buf = InMemorySSEReplayBuffer(max_sessions=2)
    first = await buf.record("s1", {"n": 1})
    await buf.record("s2", {"n": 2})
    await buf.record("s3", {"n": 3})
    # ``s1`` is the least recently written, so it is what goes.
    assert await _drain(buf.replay("s1", first)) == []
    assert await _drain(buf.replay("s3", "0" * 16)) == [("0" * 15 + "1", {"n": 3})]


async def test_the_session_evicted_is_the_least_recently_written() -> None:
    """Writing is what marks a session live: a long-lived stream that is still
    receiving events must not be dropped for a newer one that arrived once."""
    buf = InMemorySSEReplayBuffer(max_sessions=2)
    await buf.record("s1", {"n": 1})
    await buf.record("s2", {"n": 2})
    second = await buf.record("s1", {"n": 3})
    await buf.record("s3", {"n": 4})
    assert await _drain(buf.replay("s2", "0" * 16)) == []
    # ``s1``'s own numbering is untouched by the eviction of another session.
    assert second == "0" * 15 + "2"
    assert await _drain(buf.replay("s1", "0" * 16)) == [
        ("0" * 15 + "1", {"n": 1}),
        ("0" * 15 + "2", {"n": 3}),
    ]


async def test_an_unusable_session_bound_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="max_sessions must be positive"):
        InMemorySSEReplayBuffer(max_sessions=0)
