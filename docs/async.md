# Async deployment

`MCPServer` exposes two URL trees that mount the same registries, auth backend,
and session store. Pick one based on infrastructure:

| Mount | Use when |
| --- | --- |
| `server.urls` (default) | WSGI, mixed sync/async work, simplicity |
| `server.async_urls` | ASGI, high-concurrency workloads, async-native services |

Both speak the same MCP wire format — clients cannot tell the deployments
apart. The difference is internal: `async_urls` dispatches I/O-bound handlers
through `arun_service` / `arun_selector` so a single Python process can serve
many concurrent MCP calls without a thread pool.

## Switch to ASGI

```python title="urls.py"
from django.urls import include, path

from invoices.mcp import server

urlpatterns = [
    # Old: path("mcp/", include(server.urls)),
    path("mcp/", include(server.async_urls)),
]
```

Run with an ASGI server:

```bash
uvicorn myproject.asgi:application
# or
daphne -b 0.0.0.0 -p 8000 myproject.asgi:application
# or
hypercorn myproject.asgi:application --bind 0.0.0.0:8000
```

A standard `myproject/asgi.py` from `django-admin startproject` works without
modification — Django's `get_asgi_application()` happily serves async views.

## Sync collaborators are bridged automatically

`AsyncStreamableHttpView` accepts the same auth backend and session store as
the sync view. Sync methods on those collaborators are wrapped in
`asgiref.sync.sync_to_async` at the call site, so the existing
`AllowAnyBackend`, `DjangoOAuthToolkitBackend`, `InMemorySessionStore`, and
`DjangoCacheSessionStore` work unchanged.

If you write a genuinely async backend or store — e.g. one that hits a remote
IDP via `httpx.AsyncClient` — declare its methods `async def` and they are
awaited directly without the thread hop:

```python
class HttpxAuthBackend:
    async def authenticate(self, request):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://idp.example/userinfo",
                headers={"Authorization": request.META.get("HTTP_AUTHORIZATION", "")},
            )
        if response.status_code != 200:
            return None
        return TokenInfo(user=response.json()["sub"], scopes=())

    def protected_resource_metadata(self):
        return {"resource": "https://example.com/mcp/"}

    def www_authenticate_challenge(self, *, scopes=None, error=None):
        return 'Bearer realm="mcp"'

server = MCPServer(name="my-app", auth_backend=HttpxAuthBackend())
```

The `acall` helper detects coroutine-functions at runtime via
`inspect.iscoroutinefunction` and routes accordingly — no marker interface
required.

## Sync vs async services

Both work under `async_urls`:

- **Async services** (`async def create_invoice(*, data)`) run native via
  `arun_service`. The full request handling stays on the event loop.
- **Sync services** (`def create_invoice(*, data)`) are dispatched through
  `sync_to_async`. Django's connection pooling handles the thread hop
  correctly; ORM calls inside the service work without `SynchronousOnlyOperation`
  errors.

The same applies to selectors. Mix freely — the dispatch path picks the right
strategy per call.

## Server-initiated push (SSE on GET)

When a session opens `GET /mcp/`, the async view returns a
`text/event-stream` response. The server pushes JSON-RPC payloads on that
stream as events; the client interprets each `data:` line as one MCP
message. Idle periods produce SSE keep-alive comments (`: keepalive`) every
~15 seconds so reverse proxies don't close the connection.

```python
# from app code (a service, a Django signal, a background task — anything
# running in the same process as the MCP server):
await server.notify(session_id, {
    "jsonrpc": "2.0",
    "method": "notifications/progress",
    "params": {"progressToken": "task-7", "value": 0.42},
})
```

`notify` returns `True` if a subscriber was attached, `False` otherwise. A
miss is normal — sessions without an open SSE stream just don't see the
event. Most callers fire-and-forget.

### Wire details

The endpoint enforces the same headers as POST: `Mcp-Protocol-Version`
required, `Mcp-Session-Id` required and validated against the session
store. Origin allowlist applies. With no broker configured (e.g. a
`MCPServer(sse_broker=None)`), GET returns 405 — spec-compliant when the
server has nothing to push.

### Single-process only in v1

The shipped `SSEBroker` is in-process. A multi-worker deployment can:

- **Keep SSE on a single process** by running one ASGI worker (or pinning
  SSE-enabled requests to one worker via session affinity). The simplest
  path and works for most apps.
- **Use a custom broker** — implement `subscribe` / `unsubscribe` /
  `publish` against Redis pub/sub, NATS, Kafka, etc., and pass it as
  `MCPServer(sse_broker=...)`. A first-party Redis adapter is on the
  Phase 7 list.

The single-subscriber rule applies per-session: re-subscribing replaces
the previous queue. There is no message replay if a client disconnects and
reconnects — clients that need durability should drive state through
`tools/call` round-trips instead.

## When sync is the right answer

If you don't have async-native services and aren't running ASGI today,
`server.urls` is the simpler path. Switching to async without genuine async
work below the dispatch layer adds complexity (thread pool, connection
management, more failure modes) without buying anything observable.
