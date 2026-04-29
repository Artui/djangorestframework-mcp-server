"""Service callable that kicks off a "long-running" job.

The work is a ``threading.Thread + time.sleep`` stand-in for what would
realistically be a Celery / RQ / Dramatiq dispatch. The point of the
example is the **notification pattern**, not the worker harness.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from django.utils import timezone

from jobs.models import Job


def start_job(*, data: dict[str, Any], request: Any) -> Job:
    """Create a job, schedule its work, and return immediately."""
    job: Job = Job.objects.create(duration_seconds=data["duration_seconds"])

    # The MCP server is wired into the request via the URL conf — we
    # reach it through the project's URL conf module. In a real project
    # you'd inject it via a kwargs provider on the spec instead.
    from job_status.urls import server  # late import to avoid circular at module load

    session_id: str | None = request.headers.get("Mcp-Session-Id")

    threading.Thread(
        target=_run_in_background,
        args=(job.id, session_id, server),
        daemon=True,
    ).start()
    return job


def _run_in_background(job_id: Any, session_id: str | None, server: Any) -> None:
    """Run the job, transition its status, and push an SSE notification."""
    try:
        job: Job = Job.objects.get(pk=job_id)
        job.status = "running"
        job.save(update_fields=["status"])

        time.sleep(job.duration_seconds)

        job.status = "done"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])
    except Exception as exc:  # noqa: BLE001
        Job.objects.filter(pk=job_id).update(status="failed", error=str(exc))

    if session_id:
        # ``MCPServer.notify`` is a coroutine; bridge to sync via a
        # short-lived loop. In an async worker (Celery's async task,
        # Dramatiq middleware, etc.) you'd ``await`` it directly.
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": "notifications/jobs/done",
            "params": {"job_id": str(job_id)},
        }
        asyncio.run(server.notify(session_id, payload))
