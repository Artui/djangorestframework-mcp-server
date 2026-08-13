from __future__ import annotations

import hashlib
import time
from typing import Any

from django.core.cache import cache

from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.protocol.types.task import Task
from rest_framework_mcp.tasks.types.task_record import TaskRecord

_KEY_PREFIX: str = "drf-mcp:task:"
_NAMESPACE_DIGEST_CHARS: int = 12
# Used when a task declares no TTL: Django's cache API has no "never expire"
# every backend honours identically, and a task nobody polls would otherwise
# occupy the cache forever.
_NO_TTL_FALLBACK_SECONDS: int = 60 * 60 * 24 * 7


class DjangoCacheTaskStore:
    """Task store backed by ``django.core.cache``. **The default.**

    Cross-process, which for tasks is not a nice-to-have: the web worker that
    creates a task and the worker that finishes it are different processes, so
    a store they do not share cannot work at all (see
    [`InMemoryTaskStore`][rest_framework_mcp.tasks.in_memory_task_store.InMemoryTaskStore]).

    **Namespacing** follows [`DjangoCacheSessionStore`][rest_framework_mcp.transport.django_cache_session_store.DjangoCacheSessionStore] exactly — the
    server's ``name``, hashed into the key prefix, so two servers in one project
    cannot read or overwrite each other's tasks. The digest is for key hygiene
    (``name`` is free-form; memcached rejects spaces and caps length), not
    secrecy.

    **Records are serialised to plain dicts rather than pickled.** The cache
    holds them across a deploy, and a pickled dataclass is a version of this
    package's class definition: rename a field and every in-flight task becomes
    unreadable exactly when a worker tries to finish it. Plain dicts also keep
    the store usable under a JSON-serialising backend.

    **Expiry** is absolute, stamped once at ``create`` from ``ttlMs`` and
    carried in the envelope, so ``save`` renews the cache timeout to the
    *remaining* lifetime rather than restarting the clock — otherwise a task
    reporting progress often would never expire.
    """

    def __init__(self, *, namespace: str | None = None) -> None:
        self._prefix: str = (
            _KEY_PREFIX if namespace is None else f"{_KEY_PREFIX}{_digest(namespace)}:"
        )

    def _key(self, task_id: str) -> str:
        return f"{self._prefix}{task_id}"

    def create(self, record: TaskRecord) -> None:
        ttl_ms: int | None = record.task.ttl_ms
        expires_at: float | None = None if ttl_ms is None else time.time() + ttl_ms / 1000
        self._write(record, expires_at)

    def get(self, task_id: str) -> TaskRecord | None:
        raw: Any = cache.get(self._key(task_id))
        if not isinstance(raw, dict):
            return None
        return _deserialize(raw)

    def save(self, record: TaskRecord) -> None:
        raw: Any = cache.get(self._key(record.task_id))
        expires_at: Any = raw.get("expiresAt") if isinstance(raw, dict) else None
        self._write(record, expires_at if isinstance(expires_at, (int, float)) else None)

    def delete(self, task_id: str) -> None:
        cache.delete(self._key(task_id))

    def _write(self, record: TaskRecord, expires_at: float | None) -> None:
        if expires_at is None:
            timeout: float = _NO_TTL_FALLBACK_SECONDS
        else:
            # Floor at one second: a zero timeout means "expire immediately" on
            # several backends, which would drop a task the caller is still
            # writing to.
            timeout = max(1.0, expires_at - time.time())
        payload: dict[str, Any] = _serialize(record)
        payload["expiresAt"] = expires_at
        cache.set(self._key(record.task_id), payload, timeout=timeout)


def _serialize(record: TaskRecord) -> dict[str, Any]:
    task: Task = record.task
    return {
        "taskId": task.task_id,
        "status": task.status.value,
        "createdAt": task.created_at,
        "lastUpdatedAt": task.last_updated_at,
        "ttlMs": task.ttl_ms,
        "pollIntervalMs": task.poll_interval_ms,
        "statusMessage": task.status_message,
        "result": task.result,
        "error": task.error,
        "inputRequests": task.input_requests,
        "toolName": record.tool_name,
        "arguments": record.arguments,
        "principalId": record.principal_id,
        "userPk": record.user_pk,
        "scopes": list(record.scopes),
        "audience": record.audience,
        "enqueued": record.enqueued,
        "inputResponses": record.input_responses,
        "progress": record.progress,
        "total": record.total,
    }


def _deserialize(raw: dict[str, Any]) -> TaskRecord | None:
    """Rebuild a record, or ``None`` if the entry cannot be trusted.

    An entry written by a different version of this package, or by anything else
    sharing the cache, is treated as a task that does not exist rather than
    raised on: the caller answers ``-32602``, which is what a client holding an
    id that no longer resolves would have got had the entry simply expired.
    """
    try:
        status: TaskStatus = TaskStatus(raw["status"])
        task = Task(
            task_id=raw["taskId"],
            status=status,
            created_at=raw["createdAt"],
            last_updated_at=raw["lastUpdatedAt"],
            ttl_ms=raw.get("ttlMs"),
            poll_interval_ms=raw.get("pollIntervalMs"),
            status_message=raw.get("statusMessage"),
            result=raw.get("result"),
            error=raw.get("error"),
            input_requests=raw.get("inputRequests"),
        )
        return TaskRecord(
            task=task,
            tool_name=raw["toolName"],
            arguments=raw.get("arguments") or {},
            principal_id=raw["principalId"],
            user_pk=raw.get("userPk"),
            scopes=tuple(raw.get("scopes") or ()),
            audience=raw.get("audience"),
            enqueued=bool(raw.get("enqueued")),
            input_responses=raw.get("inputResponses") or {},
            progress=raw.get("progress"),
            total=raw.get("total"),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _digest(namespace: str) -> str:
    return hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:_NAMESPACE_DIGEST_CHARS]


__all__ = ["DjangoCacheTaskStore"]
