# Swap the session store

By default `MCPServer` uses `DjangoCacheSessionStore` — sessions live in
`django.core.cache`, which works in multi-process deployments without extra
infra. Two situations call for swapping it:

- **Tests** want a clean, per-instance store so test ordering doesn't leak
  state.
- **Production at scale** may want sessions in Redis directly (so they survive
  cache evictions) or backed by a persistent table.

## In tests: per-instance in-memory store

```python
from rest_framework_mcp import MCPServer
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

server = MCPServer(name="test", session_store=InMemorySessionStore())
```

The `InMemorySessionStore` keeps state on the instance, so multiple servers in
one process don't share sessions. (This is one of the reasons the package
forbids module/class-level mutable state — see CLAUDE.md.)

## In production: a custom store

Implement the `SessionStore` Protocol (four methods, no other contract).
Since 0.7 every session is bound to the principal that initialized it:
`create` receives a keyword-only `principal_id` and `owner` returns it —
the transport compares it on every POST / GET / DELETE and treats a
wrong-principal presentation exactly like an unknown session (404):

```python
from rest_framework_mcp.transport.types.session_store import SessionStore


class RedisSessionStore:
    def __init__(self, redis_client, ttl_seconds: int = 86400) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    def create(self, *, principal_id: str) -> str:
        import secrets
        token = secrets.token_urlsafe(24)
        self._redis.set(f"mcp:session:{token}", principal_id, ex=self._ttl)
        return token

    def exists(self, session_id: str) -> bool:
        return self._redis.exists(f"mcp:session:{session_id}") == 1

    def owner(self, session_id: str) -> str | None:
        value = self._redis.get(f"mcp:session:{session_id}")
        return value.decode() if value is not None else None

    def destroy(self, session_id: str) -> None:
        self._redis.delete(f"mcp:session:{session_id}")
```

Pass the instance when you build the server:

```python
server = MCPServer(name="my-app", session_store=RedisSessionStore(redis_client))
```

!!! note "Namespace it if you mount more than one server"
    The default `DjangoCacheSessionStore` keys its entries under the server's
    `url_namespace`, so two mounts can't see each other's sessions. A store you
    build yourself is yours to namespace — give each server its own key space,
    or a session minted at `/public/mcp` will satisfy `/internal/mcp`.
