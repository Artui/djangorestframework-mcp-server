from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import TaskStatus


@dataclass(frozen=True)
class Task:
    """The wire shape of a task, in every message that carries one.

    One type serves three roles the spec names separately — the
    ``CreateTaskResult`` body, the ``tasks/get`` result and the
    ``notifications/tasks`` params — because they are the same object with the
    same fields. The spec's ``WorkingTask`` / ``InputRequiredTask`` /
    ``CompletedTask`` / ``FailedTask`` / ``CancelledTask`` split is only *which
    extra field is present*, which is a property of :attr:`status`;
    :meth:`to_dict` emits the right one and refuses the wrong one.

    **Timestamps are ISO 8601 strings, stored as strings.** They come from the
    store and go out verbatim; nothing here parses or compares them. A
    ``datetime`` would invite a comparison against ``now()``, and TTL expiry
    belongs to the store — the only component that knows its backend's clock.

    :attr:`ttl_ms` is ``None`` for "no expiry", the spec's own encoding
    (``ttlMs: number | null``) rather than an omission: the field is always
    present.
    """

    task_id: str
    status: TaskStatus
    created_at: str
    last_updated_at: str
    ttl_ms: int | None = None
    poll_interval_ms: int | None = None
    status_message: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    input_requests: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Project to the wire, carrying only the field this status licenses.

        Gated on the status rather than on whether a field happens to be set, so
        a record holding both a ``result`` and an ``error`` cannot emit a shape
        no spec variant describes.
        """
        out: dict[str, Any] = {
            "taskId": self.task_id,
            "status": self.status.value,
            "createdAt": self.created_at,
            "lastUpdatedAt": self.last_updated_at,
            "ttlMs": self.ttl_ms,
        }
        if self.poll_interval_ms is not None:
            out["pollIntervalMs"] = self.poll_interval_ms
        if self.status_message is not None:
            out["statusMessage"] = self.status_message
        if self.status is TaskStatus.COMPLETED:
            # ``{}`` rather than omitted: the spec makes ``result`` mandatory on
            # a completed task, so a client unwrapping it unconditionally would
            # break on a missing key but not on an empty object.
            out["result"] = self.result if self.result is not None else {}
        elif self.status is TaskStatus.FAILED:
            out["error"] = self.error if self.error is not None else {}
        elif self.status is TaskStatus.INPUT_REQUIRED:
            out["inputRequests"] = self.input_requests if self.input_requests is not None else {}
        return out


__all__ = ["Task"]
