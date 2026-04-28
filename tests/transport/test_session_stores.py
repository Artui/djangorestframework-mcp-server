from __future__ import annotations

from rest_framework_mcp.transport.django_cache_session_store import DjangoCacheSessionStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def test_in_memory_store_lifecycle() -> None:
    store = InMemorySessionStore()
    sid = store.create()
    assert store.exists(sid)
    store.destroy(sid)
    assert not store.exists(sid)
    # Destroying an already-destroyed id is a no-op.
    store.destroy(sid)


def test_in_memory_store_is_per_instance() -> None:
    a = InMemorySessionStore()
    b = InMemorySessionStore()
    sid = a.create()
    assert a.exists(sid)
    assert not b.exists(sid)


def test_django_cache_store_lifecycle() -> None:
    store = DjangoCacheSessionStore()
    sid = store.create()
    assert store.exists(sid)
    assert not store.exists("not-a-real-id")
    store.destroy(sid)
    assert not store.exists(sid)
