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


def _record_set_timeouts(monkeypatch) -> list:
    """Capture the ``timeout=`` every subsequent ``cache.set`` is given."""
    from django.core.cache import cache

    seen: list = []
    original = cache.set

    def spy(key, value, timeout=None, **kwargs):
        seen.append(timeout)
        return original(key, value, timeout=timeout, **kwargs)

    monkeypatch.setattr(cache, "set", spy)
    return seen


class TestSessionExpiryWindows:
    """``SESSION_TTL_SECONDS`` (idle) and ``SESSION_MAX_AGE_SECONDS`` (absolute)."""

    def test_ttl_is_configurable_per_store(self) -> None:
        store = DjangoCacheSessionStore(namespace="cfg", ttl_seconds=5)
        assert store._ttl_seconds == 5  # noqa: SLF001

    def test_reading_a_session_refreshes_its_idle_window(self, monkeypatch) -> None:
        """The point of NICE-5: a session in continuous use must not lapse.

        Asserted on the timeout the store *passes* rather than on a remaining
        TTL, because ``cache.ttl()`` is a django-redis extension and the suite
        runs on LocMem — a backend-agnostic assertion of the same intent.
        """
        store = DjangoCacheSessionStore(namespace="idle", ttl_seconds=1000)
        sid = store.create(principal_id="alice")
        timeouts = _record_set_timeouts(monkeypatch)
        assert store.owner(sid) == "alice"
        assert timeouts == [1000], "the read should have re-set the key with a full idle window"

    def test_absolute_cap_clips_the_refreshed_window(self, monkeypatch) -> None:
        """A continuously-used session still cannot outlive its max age.

        Without this the sliding window is unbounded, and a revoked principal
        stays alive for as long as it keeps talking — the binding is only ever
        checked once, at ``initialize``.
        """
        store = DjangoCacheSessionStore(namespace="cap", ttl_seconds=10_000, max_age_seconds=60)
        sid = store.create(principal_id="alice")
        timeouts = _record_set_timeouts(monkeypatch)
        assert store.owner(sid) == "alice"
        assert timeouts and timeouts[0] <= 60, "the idle window must be clipped by the max age"

    def test_a_session_past_its_absolute_cap_is_dropped(self) -> None:
        store = DjangoCacheSessionStore(namespace="expired", max_age_seconds=0)
        sid = store.create(principal_id="alice")
        assert store.owner(sid) is None
        assert not store.exists(sid), "an expired session should be evicted, not left behind"

    def test_no_absolute_cap_means_a_pure_idle_window(self, monkeypatch) -> None:
        """``None`` **in the setting** disables the cap.

        ⚠ Not the same as ``max_age_seconds=None`` on the constructor, which is
        the tri-state "defer to the setting" — the usual shape in this package,
        and easy to mistake for "no cap" when writing a test.
        """
        from django.test import override_settings

        store = DjangoCacheSessionStore(namespace="nocap", ttl_seconds=1000)
        with override_settings(REST_FRAMEWORK_MCP={"SESSION_MAX_AGE_SECONDS": None}):
            uncapped = DjangoCacheSessionStore(namespace="nocap", ttl_seconds=1000)
        assert uncapped._max_age_seconds is None  # noqa: SLF001
        sid = uncapped.create(principal_id="alice")
        timeouts = _record_set_timeouts(monkeypatch)
        assert uncapped.owner(sid) == "alice"
        assert timeouts == [1000], "with no cap the full idle window is used unclipped"
        assert store._max_age_seconds is not None  # noqa: SLF001

    def test_a_bare_principal_from_an_older_release_still_works(self) -> None:
        """Upgrading must not log every current holder out mid-deploy."""
        from django.core.cache import cache

        store = DjangoCacheSessionStore(namespace="upgrade")
        cache.set(store._key("legacy"), "alice", timeout=60)  # noqa: SLF001
        assert store.owner("legacy") == "alice"
