from __future__ import annotations

from rest_framework_mcp.transport.django_cache_session_store import DjangoCacheSessionStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def test_in_memory_store_lifecycle() -> None:
    store = InMemorySessionStore()
    sid = store.create(principal_id="user:1")
    assert store.exists(sid)
    assert store.owner(sid) == "user:1"
    store.destroy(sid)
    assert not store.exists(sid)
    assert store.owner(sid) is None
    # Destroying an already-destroyed id is a no-op.
    store.destroy(sid)


def test_in_memory_store_is_per_instance() -> None:
    a = InMemorySessionStore()
    b = InMemorySessionStore()
    sid = a.create(principal_id="user:1")
    assert a.exists(sid)
    assert not b.exists(sid)


def test_django_cache_store_lifecycle() -> None:
    store = DjangoCacheSessionStore()
    sid = store.create(principal_id="user:1")
    assert store.exists(sid)
    assert store.owner(sid) == "user:1"
    assert not store.exists("not-a-real-id")
    assert store.owner("not-a-real-id") is None
    store.destroy(sid)
    assert not store.exists(sid)


def test_django_cache_store_legacy_boolean_value_is_ownerless() -> None:
    # Pre-0.7 sessions cached ``True`` instead of a principal id; ``owner``
    # treats them as ownerless so the client re-initializes.
    from django.core.cache import cache

    cache.set("drf-mcp:session:legacy-id", True, timeout=60)
    store = DjangoCacheSessionStore()
    assert store.exists("legacy-id")
    assert store.owner("legacy-id") is None
    store.destroy("legacy-id")
