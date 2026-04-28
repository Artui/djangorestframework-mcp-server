# Write an async-native auth backend

When your authentication path is itself I/O-bound — calling out to an IDP,
querying a remote token introspection endpoint, fetching JWKs over HTTP —
you want the validation to happen on the event loop, not in a thread.
Declare the backend's methods `async def` and the dispatcher awaits them
directly.

```python
import httpx
from django.http import HttpRequest

from rest_framework_mcp import MCPAuthBackend, TokenInfo


class IntrospectingAuthBackend:
    """RFC 7662 token introspection against a remote IDP."""

    def __init__(self, *, introspection_url: str, client_id: str, client_secret: str) -> None:
        self._url = introspection_url
        self._auth = httpx.BasicAuth(client_id, client_secret)
        # Reuse the connection pool across requests — one client per backend
        # instance, lifetime tied to the MCPServer.
        self._client = httpx.AsyncClient(timeout=2.0)

    async def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        header: str = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.lower().startswith("bearer "):
            return None
        token = header.split(" ", 1)[1].strip()
        if not token:
            return None

        response = await self._client.post(
            self._url, data={"token": token}, auth=self._auth
        )
        if response.status_code != 200:
            return None
        claims = response.json()
        if not claims.get("active"):
            return None

        return TokenInfo(
            user=claims.get("sub"),
            scopes=tuple(claims.get("scope", "").split()),
            audience=claims.get("aud"),
            raw=claims,
        )

    def protected_resource_metadata(self) -> dict:
        return {
            "resource": "https://example.com/mcp/",
            "authorization_servers": ["https://idp.example/"],
            "bearer_methods_supported": ["header"],
        }

    def www_authenticate_challenge(self, *, scopes=None, error=None) -> str:
        parts = ['Bearer realm="mcp"']
        if error:
            parts.append(f'error="{error}"')
        if scopes:
            parts.append(f'scope="{" ".join(scopes)}"')
        return ", ".join(parts)
```

Wire it into the server:

```python
from rest_framework_mcp import MCPServer

server = MCPServer(
    name="my-app",
    auth_backend=IntrospectingAuthBackend(
        introspection_url="https://idp.example/oauth/introspect/",
        client_id="my-resource-server",
        client_secret="…",
    ),
)
```

Mount under `async_urls` so the backend's `authenticate` is awaited
directly instead of being wrapped in `sync_to_async`:

```python
urlpatterns = [path("mcp/", include(server.async_urls))]
```

## What about the sync transport?

The same backend works under `server.urls` — the sync view detects the
async method and bridges it via `async_to_sync`. The connection pool stays
shared, but each request blocks one worker thread on the I/O. If you're
deploying under WSGI, this is fine for low-throughput admin tools; for
production scale, prefer ASGI + `async_urls`.

## Cleanup

`httpx.AsyncClient` holds a connection pool. If your process is going to
shut down cleanly (rare for Django), expose an `aclose()` from the backend
and call it from your ASGI lifespan handler. For typical deployments the
pool is freed when the process exits.
