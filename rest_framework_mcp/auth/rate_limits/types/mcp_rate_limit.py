from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from rest_framework_mcp.auth.types.token_info import TokenInfo


@runtime_checkable
class MCPRateLimit(Protocol):
    """Per-binding rate limiter, evaluated after authentication and permissions.

    The single ``consume`` call is the gate AND the bookkeeping update — there
    is no separate "check then commit" because that pattern races under
    concurrency. Implementations decrement quotas atomically in storage and
    return the suggested ``Retry-After`` in seconds once the limit is hit
    (``0`` is legal, meaning the window resets immediately), or ``None`` to
    allow the call.

    Limiters are constructed per binding at registration time; keep them cheap
    to construct and thread-safe at evaluation. State that crosses requests
    must live in shared storage (Django cache, Redis), not on the instance,
    which is not shared across worker processes.
    """

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None: ...


__all__ = ["MCPRateLimit"]
