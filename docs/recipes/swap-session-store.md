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

Implement the `SessionStore` Protocol (three methods, no other contract):

```python
from rest_framework_mcp.transport.session_store import SessionStore


class RedisSessionStore:
    def __init__(self, redis_client, ttl_seconds: int = 86400) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    def create(self) -> str:
        import secrets
        token = secrets.token_urlsafe(24)
        self._redis.set(f"mcp:session:{token}", "1", ex=self._ttl)
        return token

    def exists(self, session_id: str) -> bool:
        return self._redis.exists(f"mcp:session:{session_id}") == 1

    def destroy(self, session_id: str) -> None:
        self._redis.delete(f"mcp:session:{session_id}")
```

Wire it through settings — the dotted path is resolved at server construction:

```python
REST_FRAMEWORK_MCP = {
    "SESSION_STORE": "myproject.mcp.RedisSessionStore",
}
```

…or pass the instance directly to `MCPServer(session_store=...)` if you need to
inject configured collaborators.
