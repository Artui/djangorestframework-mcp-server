from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework_services import arun_selector, is_async, run_selector

from rest_framework_mcp._compat.acall import acall

# The sister repo's `arun_selector` runs sync callables inline, so any ORM call
# inside them raises `SynchronousOnlyOperation` under an event loop. This routes
# the sync branch through `sync_to_async` instead, keeping native async dispatch
# for genuinely async selectors.


async def arun_selector_sync_safe(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    if is_async(fn):
        return await arun_selector(fn, kwargs)
    return await acall(run_selector, fn, kwargs)


__all__ = ["arun_selector_sync_safe"]
