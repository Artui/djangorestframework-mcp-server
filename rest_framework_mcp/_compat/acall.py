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
    (auth backend, session store, custom permissions) that are not required
    to be async-native.

    Consumers writing genuinely async backends just declare their methods
    ``async def`` — :func:`inspect.iscoroutinefunction` picks that up at
    runtime, and the thread hop is skipped.
    """
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return await sync_to_async(fn)(*args, **kwargs)


__all__ = ["acall"]
