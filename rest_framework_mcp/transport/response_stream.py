from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.http import StreamingHttpResponse
from rest_framework_services.types.progress_reporter import ProgressReporter

from rest_framework_mcp.constants import JSONRPC_VERSION, JsonRpcErrorCode
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from rest_framework_mcp.transport.sse_response import format_event, keepalive_interval_seconds

_PROGRESS_METHOD: str = "notifications/progress"


@dataclass(frozen=True)
class _Finished:
    """Queue sentinel carrying the dispatch's final JSON-RPC envelope."""

    body: dict[str, Any]


def build_response_stream(
    *,
    dispatch: Callable[[ProgressReporter], Awaitable[Any]],
    request_id: Any,
    progress_token: str | int,
    max_notifications: int,
    keepalive: float | None = None,
) -> StreamingHttpResponse:
    """Answer one POST with an SSE stream carrying progress, then the response.

    ``keepalive`` overrides the shared idle interval so tests can observe a
    keep-alive without waiting the production period out.

    **Async transport only** — a sync WSGI view cannot yield while the dispatch
    runs, so the sync viewset keeps answering ``application/json``, which the
    spec always permits.

    **The HTTP status is committed before the dispatch runs**, which costs the
    ``403`` a permission denial would otherwise carry — hence the caller's
    permission pre-flight.

    Closing the stream is the cancellation signal: the ``finally`` cancels the
    dispatch task, which is what the ``2026-07-28`` revision means by
    cancellation-by-disconnect. It cancels the *await*, not the work — a thread
    parked in a database driver's socket read is not interruptible by asyncio,
    the same caveat ``DISPATCH_TIMEOUT`` carries.
    """
    response = StreamingHttpResponse(
        _stream(
            dispatch=dispatch,
            request_id=request_id,
            progress_token=progress_token,
            max_notifications=max_notifications,
            keepalive=keepalive if keepalive is not None else keepalive_interval_seconds(),
        ),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    # Without this nginx buffers the whole response and flushes on close,
    # which defeats the point of streaming progress at all.
    response["X-Accel-Buffering"] = "no"
    return response


async def _stream(
    *,
    dispatch: Callable[[ProgressReporter], Awaitable[Any]],
    request_id: Any,
    progress_token: str | int,
    max_notifications: int,
    keepalive: float,
) -> AsyncIterator[bytes]:
    """Drain progress frames until the dispatch finishes, then send its result."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    reporter = _StreamReporter(
        loop=loop,
        queue=queue,
        # gitleaks:allow — a progress correlation id from the request body, not
        # a credential. The scanner matches on the ``token=`` shape alone.
        token=progress_token,
        remaining=max_notifications,
    )
    task: asyncio.Task[None] = asyncio.ensure_future(
        _run(dispatch(reporter), queue, request_id=request_id)
    )
    try:
        while True:
            try:
                item: Any = await asyncio.wait_for(queue.get(), timeout=keepalive)
            except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct
                # A quiet dispatch still has to hold the connection open past
                # whatever idle timeout sits in front of us.
                yield b": keepalive\n\n"
                continue
            if isinstance(item, _Finished):
                yield format_event(item.body)
                return
            yield format_event(item)
    finally:
        # Reached on client disconnect (the generator is closed) as well as on
        # the normal return above, where the task has already completed.
        if not task.done():
            task.cancel()


async def _run(coro: Awaitable[Any], queue: asyncio.Queue[Any], *, request_id: Any) -> None:
    """Await the dispatch and put its final envelope on the queue.

    An escaping exception would ordinarily become a Django ``500``, but inside
    a stream there is no status left to change and propagating would truncate
    the connection with no explanation — so it becomes an in-stream ``-32603``,
    which the client can at least read.
    """
    try:
        result: Any = await coro
    except asyncio.CancelledError:
        # The client went away, so there is nobody to report to. Re-raising is
        # what lets the task finish as cancelled rather than as a success.
        raise
    except Exception as exc:  # noqa: BLE001 — see the docstring
        body = JsonRpcResponse(
            id=request_id,
            error=JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"),
        ).to_dict()
    else:
        body = (
            JsonRpcResponse(id=request_id, error=result).to_dict()
            if isinstance(result, JsonRpcError)
            else JsonRpcResponse(id=request_id, result=result).to_dict()
        )
    # **Through ``call_soon``, not ``queue.put``.** The reporter schedules its
    # frames with ``call_soon_threadsafe``, so going through the loop's one
    # ready queue orders the final response FIFO *behind* every progress frame
    # already reported. Putting it on the queue directly does not yield, so an
    # async-native service's last report could arrive after the response it
    # preceded — the one ordering the spec's flow depends on.
    asyncio.get_running_loop().call_soon(queue.put_nowait, _Finished(body))


class _StreamReporter:
    """The :class:`ProgressReporter` handed to the dispatched callable.

    **Called from a worker thread, not the event loop.** ``adispatch_spec``
    bridges sync services off-loop and ``asyncio.Queue`` is not thread-safe, so
    every frame goes through ``call_soon_threadsafe``.

    **The counters are guarded by a lock, not by an assumption.** Today
    ``adispatch_spec`` bridges to a single thread, so the read-modify-write on
    ``_remaining`` and the monotonicity check does not race — but that is a
    property of a collaborator, and a service fanning reports across a thread
    pool would over-emit past the cap or slip a non-increasing frame through.
    """

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[Any],
        token: str | int,
        remaining: int,
    ) -> None:
        self._loop = loop
        self._queue = queue
        self._token = token
        self._remaining = remaining
        self._last: float | None = None
        self._lock = threading.Lock()

    def __call__(
        self,
        progress: float,
        *,
        total: float | None = None,
        message: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock:
            # Both decisions and both writes in one critical section: splitting
            # it would let two threads pass a check only one should.
            if self._remaining <= 0:
                # The spec asks both parties to rate-limit progress; a service
                # reporting per row over a large table would otherwise turn one
                # call into a flood of frames.
                return
            if self._last is not None and progress <= self._last:
                # The spec makes increase a MUST, so forwarding a service's
                # non-monotonic report would put *this server* in violation on
                # its behalf.
                return
            self._last = progress
            self._remaining -= 1
        params: dict[str, Any] = {"progressToken": self._token, "progress": progress}
        if total is not None:
            params["total"] = total
        if message is not None:
            params["message"] = message
        if meta:
            # The structured half rides in the notification's ``_meta``, where
            # the protocol puts extension data. Keys are the caller's to
            # namespace.
            params["_meta"] = dict(meta)
        frame: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": _PROGRESS_METHOD,
            "params": params,
        }
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, frame)
        except RuntimeError:
            # The loop is gone: the client disconnected while the service was
            # still working. A reporter must not raise into domain code that
            # has no reason to defend against it.
            return


__all__ = ["build_response_stream"]
