from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from asgiref.sync import sync_to_async


async def acall(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke ``fn`` from async code regardless of whether it's async or sync.

    Async callables are awaited directly; sync callables are dispatched to a
    thread via :func:`asgiref.sync.sync_to_async` so they don't block the
    event loop. This is the bridge the async transport uses for collaborators
    (the auth backend, the session store) that are not required to be
    async-native.

    Consumers writing genuinely async backends just declare their methods
    ``async def`` — :func:`inspect.iscoroutinefunction` picks that up at
    runtime, and the thread hop is skipped.

    ⚠ **"Whatever is wrapped in ``acall`` may be async" is the wrong reading,
    and it hid a security hole.** This bridges *the callable it is handed*, not
    the callables that one goes on to invoke. Permissions and rate limits are
    reached through the aggregate helpers ``check_permissions`` /
    ``consume_rate_limits``, which are ordinary sync functions — so
    ``acall(check_permissions, …)`` takes the thread hop and a consumer's
    ``async def has_permission`` inside it is still never awaited. That is why
    those hooks are **synchronous by contract on both transports** and are
    guarded by :func:`rest_framework_mcp._compat.reject_awaitable.reject_awaitable`
    rather than bridged here. An earlier version of this docstring listed
    "custom permissions" among the bridged collaborators; it was wrong.
    """
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return await sync_to_async(fn)(*args, **kwargs)


__all__ = ["acall"]
