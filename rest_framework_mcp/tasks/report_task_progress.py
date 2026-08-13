from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from rest_framework_services.types.progress_reporter import ProgressReporter

from rest_framework_mcp.tasks.types.task_record import TaskRecord
from rest_framework_mcp.tasks.types.task_store import TaskStore
from rest_framework_mcp.tasks.utils import now_iso


def report_task_progress(store: TaskStore, task_id: str) -> ProgressReporter:
    """A ``ProgressReporter`` that writes onto the task record.

    What ``progress`` resolves to inside a task. The inline MCP path answers a
    ``progressToken`` with ``notifications/progress``, which needs a live
    connection a worker does not have, so **the task record is the bridge**: the
    numbers land on the record, the rendered string lands on the wire ``Task``,
    and a polling client reads both through ``tasks/get``.

    **``meta`` is accepted and dropped, deliberately** — a task has no
    notification to carry it and the protocol ``Task`` has no free-form slot.
    Signature compatibility is the point: a service written against the Protocol
    must not have to know which path is executing it.

    **A terminal task is never rewritten**, by the same one-way rule as
    ``transition_task`` — a late
    report from a worker still unwinding would otherwise move ``lastUpdatedAt``
    on a finished task, and make a cancel look as if it had not taken.

    **This does not publish ``notifications/tasks``**, which says the *status
    changed*: progress is movement inside one status, so publishing per tick
    would turn a subscription into a firehose about a task that is still,
    accurately, ``working``.

    **Failures are swallowed**: the sink is network I/O called once per tick,
    and an unreachable cache must not take down the operation whose progress it
    was only describing.
    """

    def report(
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        del meta  # No slot on the wire, so no place to put it.
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
    service supplied:

        (3, 10, "Indexing")  → "Indexing (3/10)"
        (3, 10, None)        → "3/10"
        (3, None, "Indexing")→ "Indexing (3)"
        (3, None, None)      → "3"

    ``:g`` rather than ``str`` because the Protocol types these as ``float``:
    a human reads ``"3/10"`` where a float repr prints ``"3.0/10.0"``, and
    genuinely fractional values still render.
    """
    count: str = f"{progress:g}" if total is None else f"{progress:g}/{total:g}"
    return count if message is None else f"{message} ({count})"


__all__ = ["report_task_progress"]
