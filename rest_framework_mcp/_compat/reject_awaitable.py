from __future__ import annotations

import inspect
from typing import Any

from django.core.exceptions import ImproperlyConfigured


def reject_awaitable(result: Any, *, call: str, remedy: str, hazard: str) -> Any:
    """Return ``result``, or refuse it when nothing on this path will await it.

    The inverse of :func:`rest_framework_mcp._compat.acall.acall`. Wherever a
    consumer-supplied callable is invoked from a synchronous path, writing it
    ``async def`` hands back an un-awaited coroutine — and **a coroutine object
    is truthy and is never ``None``**, so every downstream test of the return
    value reads it as a yes.

    **This is a security gate, not a type check.** ``backend.authenticate()``,
    ``perm.has_permission()`` and ``perm.is_listable()`` all fail *open* that
    way: the misconfiguration is invisible in the response and the endpoint
    serves everyone. (``limiter.consume()`` and the session store fail closed
    or nonsensically instead.) Refusing converts a silent, total bypass into a
    loud misconfiguration.

    **Detection is on the returned value, not the function.**
    :func:`inspect.iscoroutinefunction` would miss a plain ``def`` that hands
    back a coroutine, a future, or any other awaitable — a decorator, a
    ``functools.partial`` over an async callable, an object with ``__await__``.

    Args:
        result: The value the consumer callable returned.
        call: The offending callable, e.g. ``"MyBackend.authenticate()"``.
        remedy: How to fix it.
        hazard: What continuing would cost.

    Raises:
        django.core.exceptions.ImproperlyConfigured: If ``result`` is awaitable.
    """
    if not inspect.isawaitable(result):
        return result
    if inspect.iscoroutine(result):
        # Closing the never-started coroutine stops CPython tacking a
        # "coroutine ... was never awaited" RuntimeWarning onto the traceback,
        # which would bury the actionable error below.
        result.close()
    raise ImproperlyConfigured(
        f"{call} returned an awaitable, but nothing on this path awaits it. "
        f"{remedy} Refusing the request: {hazard}"
    )


__all__ = ["reject_awaitable"]
