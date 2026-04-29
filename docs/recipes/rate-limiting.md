# Add rate limiting to a binding

`MCPRateLimit` is a Protocol — anything with a single
`consume(request, token) -> int | None` method qualifies. The return value
is the suggested retry-after-in-seconds when the limit has been hit, or
`None` to allow the call.

The shipped `FixedWindowRateLimit` is backed by `django.core.cache` and is
the right choice for most Django deployments — the cache is already shared
across worker processes when you're running anything serious.

```python
from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.rate_limits.fixed_window_rate_limit import (
    FixedWindowRateLimit,
)
from rest_framework_services.types.service_spec import ServiceSpec

server = MCPServer(name="my-app")

server.register_service_tool(
    name="invoices.create",
    spec=ServiceSpec(service=create_invoice, ...),
    rate_limits=[
        FixedWindowRateLimit(max_calls=120, per_seconds=60, namespace="burst"),
        FixedWindowRateLimit(max_calls=10_000, per_seconds=86_400, namespace="daily"),
    ],
)
```

Limits are AND-combined — denial from any limiter immediately rejects the
call. The handler stops at the first denial (no point further consuming
quota when the call is going to fail).

## Wire shape

Denial returns JSON-RPC error code `-32005 RATE_LIMITED` with
`data.retryAfter` set to the suggested seconds:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32005,
    "message": "Rate limit exceeded",
    "data": {"retryAfter": 42}
  }
}
```

MCP clients that respect this should back off accordingly. Misbehaving
clients keep hitting the same limit; the cache counter is per-bucket so the
counter doesn't grow unbounded.

## Custom keys

The default `FixedWindowRateLimit` keys per authenticated user (or
`REMOTE_ADDR` for anonymous requests). Override with a callable:

```python
def per_org_key(request, token) -> str:
    return f"org:{token.user.organization_id}"


server.register_service_tool(
    ...,
    rate_limits=[
        FixedWindowRateLimit(
            max_calls=10_000, per_seconds=60, key=per_org_key
        ),
    ],
)
```

## Choosing a scheme

Three implementations ship in `rest_framework_mcp.auth.rate_limits`:

- **`FixedWindowRateLimit`** — bucketed counters, atomic via
  `cache.add` + `cache.incr`. Cheaper, but a client can issue
  `2 * max_calls` across the bucket boundary.
- **`SlidingWindowRateLimit`** — list of timestamps in cache, pruned on
  every call. Smoother (limits the rolling-window rate, not bucket count),
  at the cost of a small read-modify-write race under concurrency.
- **`TokenBucketRateLimit`** — bucket of tokens that refills continuously
  at `refill_per_second`. Burst-friendly: a full bucket absorbs a sudden
  burst of `capacity` requests, then the steady-state rate clamps to
  `refill_per_second`. Useful when consumers naturally batch.

```python
from rest_framework_mcp.auth.rate_limits import (
    FixedWindowRateLimit,
    SlidingWindowRateLimit,
    TokenBucketRateLimit,
)

server.register_service_tool(
    name="invoices.create",
    spec=...,
    rate_limits=[
        # Steady-state 1 call/sec, but absorb bursts up to 60.
        TokenBucketRateLimit(capacity=60, refill_per_second=1.0),
    ],
)
```

All three share the same `consume(request, token) -> int | None` signature,
so swapping is a one-line change. The shipped classes are read-modify-write
on `django.core.cache` — for strict atomicity under heavy concurrency, back
the limiter with a Redis-Lua script implementing the same Protocol.

## Implementing your own

For leaky buckets, GCRA, distributed Lua-backed schemes etc. — implement
the Protocol against your storage of choice:

```python
from django.http import HttpRequest

from rest_framework_mcp import TokenInfo


class LeakyBucketRateLimit:
    """Naive sketch — production should use redis-py with Lua for atomicity."""

    def __init__(self, *, capacity: int, leak_per_second: float) -> None:
        self._capacity = capacity
        self._leak = leak_per_second

    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        ...
```

Pass it to a binding the same way:

```python
server.register_service_tool(..., rate_limits=[LeakyBucketRateLimit(capacity=60, leak_per_second=1)])
```

## Tips

- Limiters are **constructed once per binding** at registration time —
  shared across all dispatches. Keep `__init__` cheap.
- State that crosses requests must live in shared storage, not on the
  instance. `FixedWindowRateLimit` follows this rule (Django cache);
  custom limiters must too if they're going to work under multiple worker
  processes.
- The default Django `locmem` cache is process-local. In production, use
  Memcached or Redis so all workers see the same counter.
