from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from rest_framework_services.types.progress_reporter import ProgressReporter

from rest_framework_mcp.tasks.types.task_record import TaskRecord
from rest_framework_mcp.tasks.types.task_store import TaskStore
from rest_framework_mcp.tasks.utils import now_iso


def report_task_progress(store: TaskStore, task_id: str) -> ProgressReporter:
    """A :class:`ProgressReporter` that writes onto the task record.

    What ``progress`` resolves to inside a task. The inline MCP path answers a
    ``progressToken`` with ``notifications/progress`` — but that needs a live
    connection, and a worker has none. **The task record is the bridge**: the
    numbers land on the record, the rendered string lands on the wire ``Task``,
    and a polling client reads both through ``tasks/get``. Push becomes poll;
    nothing else about the service changes.

    ⭐ **Without this, declaring ``progress`` in a service is live-connection
    only** — which means the long-running operations most in need of it, the
    ones promoted to tasks *because* they are long, are exactly the ones it
    silently did nothing for.

    ⛔ **``meta`` is accepted and dropped, deliberately.** On the inline path it
    rides in the notification's ``_meta``; a task has no notification, and the
    protocol ``Task`` has no free-form slot to put it in. Dropping it beats
    inventing a place for it in the record that no client could ever read.
    Signature compatibility is the point — a service written against the
    Protocol must not have to know which path is executing it.

    ⚠ **A terminal task is never rewritten.** A late report from a worker still
    unwinding after the task completed, failed or was cancelled would otherwise
    move ``lastUpdatedAt`` on a finished task, and a cancel would look like it
    had not taken. Same one-way rule as
    :func:`~rest_framework_mcp.tasks.transition_task.transition_task`, for the
    same reason.

    ⚠ **This does not publish ``notifications/tasks``.** That notification says
    *the status changed*, and progress is movement inside one status —
    publishing per tick would turn a subscription into a firehose describing a
    task that is still, accurately, ``working``. The client polls; the
    ``pollIntervalMs`` it was handed is what paces it.

    ⚠ **Failures are swallowed** — an unreachable cache must not take down the
    operation whose progress it was only describing. This mirrors
    ``combine_progress``'s per-sink isolation upstream, and matters more here:
    the sink is network I/O on somebody else's box, called once per tick.
    """

    def report(
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        del meta  # See the docstring: no slot on the wire, so no place to put it.
        try:
            record: TaskRecord | None = store.get(task_id)
            if record is None or record.status.is_terminal:
                return
            updated: TaskRecord = replace(
                record,
                progress=progress,
                total=total,
                task=replace(
                    record.task,
                    status_message=_render_progress(progress, total, message),
                    last_updated_at=now_iso(),
                ),
            )
            store.save(updated)
        except Exception:  # noqa: BLE001 — a telemetry write must not fail the work.
            return

    return report


def _render_progress(progress: float, total: float | None, message: str | None) -> str:
    """The ``statusMessage`` a polling client actually sees.

    The wire ``Task`` has ``statusMessage`` and no numeric field, so this is the
    only channel to the client and it is a string. Four shapes, by what the
    service supplied::

        (3, 10, "Indexing")  → "Indexing (3/10)"
        (3, 10, None)        → "3/10"
        (3, None, "Indexing")→ "Indexing (3)"
        (3, None, None)      → "3"

    ``:g`` rather than ``str``: the Protocol types these as ``float``, and
    ``"3/10"`` is what a human reads where ``"3.0/10.0"`` is what a float
    repr prints. Genuinely fractional values still render (``2.5``).
    """
    count: str = f"{progress:g}" if total is None else f"{progress:g}/{total:g}"
    return count if message is None else f"{message} ({count})"


__all__ = ["report_task_progress"]
