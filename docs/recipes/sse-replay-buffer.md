# Resume dropped SSE streams with `Last-Event-ID`

The MCP transport supports the standard SSE
[`Last-Event-ID`](https://html.spec.whatwg.org/multipage/server-sent-events.html#last-event-id)
header so clients that drop a streaming connection can reconnect and
receive the events they missed. This is **opt-in**: pass an
:class:`SSEReplayBuffer` to `MCPServer(sse_replay_buffer=...)` and every
event published via `await server.notify(...)` is recorded with a
monotonic ID before fan-out, then replayed on the client's next GET if it
arrives carrying `Last-Event-ID`.

Without a buffer, the wire shape is unchanged — no `id:` lines, and a
client that sends `Last-Event-ID` is silently ignored (no replay, fresh
live stream).

## Single-process: `InMemorySSEReplayBuffer`

Suitable when you run a single ASGI worker. State lives on the buffer
instance, sized to a fixed-cap `deque` per session.

```python title="myproject/mcp.py"
from rest_framework_mcp import MCPServer
from rest_framework_mcp.transport.in_memory_sse_replay_buffer import (
    InMemorySSEReplayBuffer,
)

server = MCPServer(
    name="my-app",
    sse_replay_buffer=InMemorySSEReplayBuffer(max_events=2048),
)
```

`max_events` caps **per-session** retention. Events older than that drop
off the back when new ones arrive — a client that disconnects for very
long and replays from a buried event sees a partial replay (best-effort).

## Multi-worker: `RedisSSEReplayBuffer`

Pair this with `RedisSSEBroker` so any worker can both publish the live
event and serve the replay request. The buffer is backed by a
[Redis Stream](https://redis.io/docs/data-types/streams/) per session:
`XADD MAXLEN ~ N` for record (capped, approximate trimming),
`XRANGE (<id> +` for replay.

```python title="myproject/mcp.py"
from redis.asyncio import Redis

from rest_framework_mcp import MCPServer
from rest_framework_mcp.transport.redis_sse_broker import RedisSSEBroker
from rest_framework_mcp.transport.redis_sse_replay_buffer import (
    RedisSSEReplayBuffer,
)


client = Redis.from_url("redis://localhost:6379/0")

server = MCPServer(
    name="my-app",
    sse_broker=RedisSSEBroker(client),
    sse_replay_buffer=RedisSSEReplayBuffer(client, max_events=4096),
)
```

The two collaborators are independent: you could pair an in-memory
broker with a Redis buffer, or vice versa. They're decoupled because
brokers handle live fan-out and buffers handle resume — different
concerns, different storage trade-offs.

## What the wire looks like

With a buffer wired in, every `notify` produces an SSE frame carrying
both `id:` and `data:`:

```
id: 0000000000000007
data: {"event":"job-finished","jobId":42}

id: 0000000000000008
data: {"event":"job-finished","jobId":43}
```

On reconnect the client sends:

```
Last-Event-ID: 0000000000000007
```

…and the server replays event 8 (and any later events) before resuming
live mode. Browser `EventSource` does this automatically; programmatic
SSE clients should preserve the latest `id:` they received and echo it
back on retry.

## Choosing `max_events`

Pick a number that covers your worst-case disconnect window times your
peak event rate per session. Examples:

- "5-minute reconnect window, 10 events/sec peak" → `max_events ≥ 3000`.
- "1-minute reconnect window, 1 event/sec average" → `max_events ≥ 60`
  (round up for headroom).

Per-session, so total memory scales with `(active sessions) × max_events
× avg_payload_size`. The Redis variant is also bounded but uses
approximate trimming — actual retention may be slightly above the cap
between trim events.

## Custom buffers

`SSEReplayBuffer` is a small Protocol — three methods: `record`,
`replay`, `forget`. Implement it against any storage you like (PostgreSQL
LISTEN/NOTIFY, NATS JetStream, Kafka with a consumer group key, …). Pass
your instance via `sse_replay_buffer=` and the rest of the package works
unchanged.

## Caveats

- **Bounded retention.** A client that disconnects for hours and tries
  to resume from an evicted ID will get a partial replay. The MCP spec
  is silent on how the client should detect this; in practice clients
  treat any successful resume as "OK" and rely on application-level
  consistency (e.g. a follow-up `tools/call` to fetch latest state).
- **DELETE forgets the buffer.** When a client tears the session down
  with `DELETE /mcp/`, the transport calls `buffer.forget(session_id)`
  so dead sessions don't accumulate state. Buffers can no-op this if
  they prefer TTL-based eviction.
- **No replay without a buffer.** This is a deliberate v1 default — most
  deployments don't need resume, and recording every event has cost.
  Opt in when you need it.
