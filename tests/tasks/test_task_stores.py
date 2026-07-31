"""Both task stores, against the same expectations.

The in-memory one is a development convenience and the cache-backed one is the
default, but a caller cannot tell them apart — so most of this runs against
both, and only the properties that are genuinely backend-specific (expiry,
serialisation, namespacing) get their own tests.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from django.core.cache import cache

from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.protocol.types.task import Task
from rest_framework_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.tasks.types.task_record import TaskRecord


def _record(task_id: str = "t1", **task_kwargs: Any) -> TaskRecord:
    defaults: dict[str, Any] = {
        "task_id": task_id,
        "status": TaskStatus.WORKING,
        "created_at": "2026-07-31T10:00:00Z",
        "last_updated_at": "2026-07-31T10:00:00Z",
        "ttl_ms": 60_000,
        "poll_interval_ms": 500,
    }
    defaults.update(task_kwargs)
    return TaskRecord(
        task=Task(**defaults),
        tool_name="things.do",
        arguments={"a": 1},
        principal_id="user:7",
        user_pk=7,
        scopes=("things:write",),
        audience="https://example.test/mcp",
    )


@pytest.fixture(params=["memory", "cache"])
def store(request: Any) -> Any:
    if request.param == "memory":
        return InMemoryTaskStore()
    cache.clear()
    return DjangoCacheTaskStore(namespace="s")


# ----- the contract both backends keep -----


def test_a_created_task_reads_back(store: Any) -> None:
    store.create(_record())
    assert store.get("t1") is not None


def test_everything_needed_to_replay_the_call_survives(store: Any) -> None:
    """The record is only worth storing if the worker can act on it."""
    store.create(_record())
    back: TaskRecord = store.get("t1")
    assert back.tool_name == "things.do"
    assert back.arguments == {"a": 1}
    assert back.principal_id == "user:7"
    assert back.user_pk == 7
    assert back.scopes == ("things:write",)
    assert back.audience == "https://example.test/mcp"


def test_an_unknown_id_is_none_not_an_error(store: Any) -> None:
    assert store.get("nope") is None


def test_save_overwrites(store: Any) -> None:
    store.create(_record())
    store.save(_record().with_task(status=TaskStatus.COMPLETED, result={"ok": 1}))
    back: TaskRecord = store.get("t1")
    assert back.status is TaskStatus.COMPLETED
    assert back.task.result == {"ok": 1}


def test_delete_removes(store: Any) -> None:
    store.create(_record())
    store.delete("t1")
    assert store.get("t1") is None


def test_deleting_an_unknown_id_is_not_an_error(store: Any) -> None:
    store.delete("never-existed")


def test_two_tasks_do_not_collide(store: Any) -> None:
    store.create(_record("a"))
    store.create(_record("b"))
    assert store.get("a").task_id == "a"
    assert store.get("b").task_id == "b"


# ----- cache-backed specifics -----


def test_the_cache_store_holds_a_plain_dict_not_a_pickled_dataclass() -> None:
    """A pickled dataclass would be a copy of *this version's* class.

    Rename a field and every in-flight task becomes unreadable at exactly the
    moment a worker tries to finish it.
    """
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record())
    raw: Any = _envelope("t1")
    assert isinstance(raw, dict)
    assert raw["status"] == "working"


def test_save_renews_the_remaining_lifetime_not_the_whole_ttl() -> None:
    """⚠ The bug this prevents: a task reporting often would never expire.

    Expiry is stamped absolutely at creation and carried in the envelope, so a
    later write shortens the cache timeout rather than restarting the clock.
    """
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record(ttl_ms=60_000))
    first: Any = _envelope("t1")["expiresAt"]
    time.sleep(0.01)
    store.save(_record(ttl_ms=60_000).with_task(status_message="tick"))
    assert _envelope("t1")["expiresAt"] == first


def test_a_task_with_no_ttl_still_gets_a_backstop() -> None:
    """``None`` means "no expiry" on the wire; the cache still needs a bound,
    or an un-polled task pins memory forever."""
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record(ttl_ms=None))
    assert _envelope("t1")["expiresAt"] is None
    assert store.get("t1") is not None


def test_saving_a_task_the_cache_has_already_dropped_still_writes() -> None:
    """The expiry lookup misses, so the write falls back to the no-TTL bound
    rather than failing — the alternative is losing a worker's result."""
    cache.clear()
    store = DjangoCacheTaskStore(namespace="s")
    store.save(_record())
    assert store.get("t1") is not None


def test_an_already_expired_absolute_deadline_does_not_write_a_zero_timeout() -> None:
    """A zero timeout means "expire immediately" on several backends, which
    would drop the entry the caller is mid-way through writing."""
    cache.clear()
    store = DjangoCacheTaskStore(namespace="s")
    store.create(_record(ttl_ms=1))
    time.sleep(0.01)
    store.save(_record().with_task(status=TaskStatus.COMPLETED))
    assert store.get("t1") is not None


def test_an_unreadable_entry_reads_as_a_task_that_does_not_exist() -> None:
    """Written by another version, or by something else sharing the cache.

    ``-32602`` is the honest answer — the same one an expired task gets — and
    it beats raising out of a poll the client cannot fix.
    """
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record())
    cache.set(_key("t1"), {"taskId": "t1", "status": "not-a-status"})
    assert store.get("t1") is None


def test_a_missing_field_is_also_unreadable_rather_than_a_crash() -> None:
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record())
    cache.set(_key("t1"), {"status": "working"})
    assert store.get("t1") is None


def test_a_non_dict_entry_is_unreadable() -> None:
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record())
    cache.set(_key("t1"), "clobbered")
    assert store.get("t1") is None


def test_two_servers_in_one_project_cannot_see_each_others_tasks() -> None:
    """The same namespacing the session store does, for the same reason: one
    flat key space would let either server read or destroy the other's work."""
    cache.clear()
    a = DjangoCacheTaskStore(namespace="server-a")
    b = DjangoCacheTaskStore(namespace="server-b")
    a.create(_record())
    assert b.get("t1") is None
    assert a.get("t1") is not None


def test_a_free_form_server_name_still_produces_a_usable_key() -> None:
    """``name`` is consumer-supplied prose; memcached rejects spaces and caps
    length. Hashing it is key hygiene, not secrecy."""
    cache.clear()
    store = DjangoCacheTaskStore(namespace="My Invoicing Server ✨")
    store.create(_record())
    assert store.get("t1") is not None


def test_a_store_built_without_a_namespace_works() -> None:
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record())
    assert store.get("t1") is not None


# The un-namespaced key format, which the store documents: a namespace is
# hashed into the prefix, and without one the key is the id straight after it.
def _key(task_id: str) -> str:
    return f"drf-mcp:task:{task_id}"


def _envelope(task_id: str) -> Any:
    return cache.get(_key(task_id))
