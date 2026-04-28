from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework_services._compat.arun_service import arun_service
from rest_framework_services._compat.is_async import is_async
from rest_framework_services._compat.run_service import run_service
from rest_framework_services.selectors.utils import arun_selector, run_selector

from rest_framework_mcp._compat.acall import acall

# These thin wrappers exist because the sister repo's `arun_service` /
# `arun_selector` route sync callables in ways that don't compose cleanly with
# the Django async ORM:
#   - `arun_service` with atomic=True wraps the callable in `async_to_sync`,
#     which only works for async callables (sync ones raise "can't be awaited").
#   - `arun_selector` runs sync callables inline, so any ORM call inside them
#     raises `SynchronousOnlyOperation` under an event loop.
# Both helpers below detect sync vs async at the boundary and route through
# `sync_to_async` (via :func:`acall`) for the sync branch — preserving native
# async-dispatch performance for genuinely async services and selectors.


async def arun_service_sync_safe(
    fn: Callable[..., Any], kwargs: dict[str, Any], *, atomic: bool
) -> Any:
    if is_async(fn):
        return await arun_service(fn, kwargs, atomic=atomic)
    return await acall(run_service, fn, kwargs, atomic=atomic)


async def arun_selector_sync_safe(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    if is_async(fn):
        return await arun_selector(fn, kwargs)
    return await acall(run_selector, fn, kwargs)


__all__ = ["arun_selector_sync_safe", "arun_service_sync_safe"]
