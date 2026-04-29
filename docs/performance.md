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

## Adding profile points

The package emits OpenTelemetry spans for `mcp.tools.call`,
`mcp.resources.read`, and `mcp.prompts.get` when the `[otel]` extra is
installed. See [`docs/observability.md`](observability.md) — that's
the right tool for measuring real-world latency.
