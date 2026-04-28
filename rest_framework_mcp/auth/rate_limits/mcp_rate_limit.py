from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.http import HttpRequest

from rest_framework_mcp.auth.token_info import TokenInfo


@runtime_checkable
class MCPRateLimit(Protocol):
    """Per-binding rate limiter, evaluated after authentication and permissions.

    The single ``consume`` call is the gate AND the bookkeeping update — there
    is no separate "check then commit" because that pattern races under
    concurrency. Implementations decrement quotas atomically in storage; the
    return value is the suggested ``Retry-After`` in seconds when the limit
    has been hit, or ``None`` to allow the call.

    Returning ``0`` is allowed (e.g. "denied but window resets immediately")
    but most implementations will return a positive integer.

    Limiters are constructed per-binding at registration time; keep them cheap
    to construct and thread-safe at evaluation. State that crosses requests
    must live in shared storage (Django cache, Redis, …), not on the
    instance — instance state would be lost the moment the binding is
    re-used across worker processes.
    """

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None: ...


__all__ = ["MCPRateLimit"]
