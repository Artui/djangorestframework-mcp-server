# Performance baseline

Where the time goes on a `tools/call`. The numbers below are a single
data point — what they're really for is locating overhead by layer, so
you know where to look first if you ever need to optimise.

## Layers

A `tools/call` traverses three layers:

```
HTTP request
   │
   │ ① Transport — origin / protocol-version / session validation,
   │    JSON-RPC parsing, response serialization
   ▼
Dispatch
   │
   │ ② Handler — name lookup, permissions, rate limits, input validation,
   │    kwarg-pool resolution, output serializer, ToolResult shaping
   ▼
Service callable
       ③ — your code
```

The benchmark in `scripts/benchmark.py` runs the same trivial service
(`def _service(*, data) -> {"result": data["n"] * 2}`) through each
layer and reports the per-call median.

## A run on this machine

Python 3.14, MacBook (Apple silicon), in-memory SQLite, fresh
`InMemorySessionStore`, no auth backend overhead (`AllowAnyBackend`):

| Path                     | Median (µs)   | Overhead vs direct |
|--------------------------|---------------|--------------------|
| Direct callable          | ~0.1          | 1×                 |
| Handler dispatch only    | ~50           | ~500×              |
| Full HTTP round-trip     | ~170          | ~1,800×            |

Run it locally:

```bash
uv run python scripts/benchmark.py
```

## What to take away

- **Per-call overhead is in the tens of microseconds at the handler
  layer and ~170 µs end-to-end** for an in-process Django test client.
  For typical MCP workloads — LLM agents calling tools at human speed
  — this is several orders of magnitude below what matters. Spending
  effort optimising the dispatch path is almost never worth it.
- **The dominant cost on real servers is the service callable** — a
  database round-trip, a remote API call, or LLM-shaped output
  rendering. Optimise those.
- **The transport accounts for roughly a third of the total** in this
  micro-benchmark (~120 µs out of ~170 µs). Most of that is Django
  request/response construction and JSON-RPC envelope parsing — the
  same cost any Django view pays. Async dispatch (`server.async_urls`)
  shifts I/O off the request thread but doesn't materially change
  per-call CPU.

## Where to look if a real workload is slow

A few specific things that can show up at scale:

- **Auth backend** — if every call hits an external introspection
  endpoint, that's the bottleneck. Cache the introspection result in
  Django's cache (per-token TTL) and reuse across requests.
- **Output serializer** — `ModelSerializer(many=True)` on a list of
  thousands is N+1-prone. Add `.select_related()` /
  `.prefetch_related()` in the selector; use `output_format="toon"`
  to reduce token count if the bottleneck is downstream LLM cost.
- **`atomic=True` on a service that doesn't write** — services
  default to wrapping in `transaction.atomic()`. If a service is
  read-shaped, set `atomic=False` on the spec, or — better — register
  it as a **selector tool** so the read pipeline runs without the
  transaction overhead.
- **SSE broker** — `InMemorySSEBroker` pushes are sub-µs; Redis pub/sub
  adds ~1 ms per `notify`. Acceptable for nearly all use cases; if you
  do hit a hot path, batch notifications.

## What the package bounds

Inbound work has been bounded since the beginning — `MAX_REQUEST_BYTES` rejects
an oversized body with `413` before parsing. Outbound work is bounded by three
settings, all of which take `None` to disable and all of which can be overridden
per tool at registration:

| Bound | Setting | Per-tool | Behaviour over the bound |
|---|---|---|---|
| Result size | `MAX_RESULT_BYTES` (5 MiB) | `max_result_bytes=` | `isError` result naming the remedy |
| Page size | `MAX_PAGE_SIZE` (500) | `max_page_size=` | `limit` clamped down; `hasNext` says there's more |
| Duration | `DISPATCH_TIMEOUT` (60 s) | `dispatch_timeout=` | `isError` result; ⚠ ASGI only |

Three things worth knowing before you tune them:

**Every payload goes out twice.** A successful tool result carries the payload
as `structuredContent` *and* as the `content[0]` text mirror the spec asks for,
so the context cost at the client is roughly 2× the payload. If you are fighting
a context window rather than a byte ceiling, `INCLUDE_STRUCTURED_CONTENT=False`
(server-wide or per binding) halves it at once — clients that don't parse the
structured field lose nothing.

**A deadline does not reclaim the worker.** `DISPATCH_TIMEOUT` cancels the
asyncio task, but a thread parked in `psycopg`'s socket read — which is where
every ORM-backed spec spends its time — is not interruptible by asyncio, so the
thread stays hot until the *query* ends. The deadline buys the client a terminal
answer instead of an open request; it does not free the connection. Set a
database-level statement timeout for that half:

```python
DATABASES = {
    "default": {
        # …
        "OPTIONS": {"options": "-c statement_timeout=30000"},  # PostgreSQL, ms
    }
}
```

**Truncation is never the answer.** Over a ceiling, a call fails with an error
the model can act on ("narrow the filter, lower `limit`") rather than returning
a shortened payload. A clipped list looks complete to a model, which then
reasons from it — a wrong answer delivered confidently is worse than a failed
call.

### What is *not* bounded

- **Query cost.** Nothing here stops a selector from issuing an expensive join;
  the bounds measure the result, not the work. `select_related` /
  `prefetch_related` and a database statement timeout are the tools for that.
- **Unpaginated LIST tools**, except by `MAX_RESULT_BYTES`. A `paginate=False`
  selector serialises everything its selector resolves to, and it cannot be
  clamped honestly — the result has nowhere to record that rows were dropped.
  Registering one emits `UnboundedListWarning`; `REQUIRE_LIST_PAGINATION=True`
  makes it an error.
- **Concurrency.** Bounding one call says nothing about how many run at once.
  Rate limits (`rate_limits=` per binding) are the lever there.

## Adding profile points

The package emits OpenTelemetry spans for `mcp.tools.call`,
`mcp.resources.read`, and `mcp.prompts.get` when the `[otel]` extra is
installed. See [`docs/observability.md`](observability.md) — that's
the right tool for measuring real-world latency.
