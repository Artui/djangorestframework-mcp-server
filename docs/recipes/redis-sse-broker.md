# Multi-worker SSE with Redis

The default :class:`InMemorySSEBroker` works for single-process deployments —
one ASGI worker, one event loop, no fan-out concerns. Multi-worker setups
need an out-of-process broker so any worker can deliver a `notify(...)` to
whichever worker is holding the streaming GET.

The shipped :class:`RedisSSEBroker` is the production answer.

## Install

```bash
pip install "djangorestframework-mcp-server[redis]"
```

Pulls in `redis>=5.0` (which provides `redis.asyncio`).

## Wire it up

```python title="myproject/mcp.py"
from redis.asyncio import Redis

from rest_framework_mcp import MCPServer
from rest_framework_mcp.transport.redis_sse_broker import RedisSSEBroker


redis_client = Redis.from_url("redis://localhost:6379/0", decode_responses=False)

server = MCPServer(
    name="my-app",
    sse_broker=RedisSSEBroker(redis_client),
)
```

That's the only change. `server.async_urls` will now route SSE through
Redis pub/sub. The wire shape is identical from the client's perspective —
`GET /mcp/` still streams `data:` events; `await server.notify(...)` still
returns whether a subscriber was attached.

## How it works

For each session that opens a streaming GET, the broker spawns a background
`asyncio.Task` that subscribes to the per-session Redis channel
(`drf-mcp:sse:<session_id>` by default). The task pumps incoming pub/sub
messages onto a per-session `asyncio.Queue`, which the streaming generator
consumes — same interface as the in-memory broker.

`publish` is a single Redis `PUBLISH` call. Any worker that calls
`await server.notify(session_id, ...)` reaches the right session because
every worker subscribes to the same channel.

## Caveats

- **Single subscriber per session.** Same contract as the in-memory broker.
  If you scale to multiple workers and a session reconnects after a network
  hiccup, the new worker's subscriber replaces the old. The dropped
  subscriber's task on the original worker eventually unwinds (cancelled by
  `unsubscribe` or by Redis client shutdown).
- **Replay is opt-in.** Messages published while no subscriber was attached
  are dropped *unless* you configure an :class:`SSEReplayBuffer` — see
  the [Last-Event-ID resume recipe](sse-replay-buffer.md).
- **Channel-prefix isolation.** Pass `channel_prefix="env-staging"` (or
  similar) to keep dev / staging / prod traffic in the same Redis instance
  from cross-talking.
- **Client lifecycle is yours.** Close the Redis client during ASGI
  lifespan shutdown — the broker doesn't own it.

## Custom brokers

`RedisSSEBroker` is just a class that implements the :class:`SSEBroker`
Protocol (four methods: `subscribe`, `unsubscribe`, `publish`,
`has_subscriber`). NATS-, Kafka-, RabbitMQ-backed brokers fit the same
interface — just keep the single-subscriber-per-session contract.
