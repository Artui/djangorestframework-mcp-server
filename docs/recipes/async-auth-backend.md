# Write an async-native auth backend

When your authentication path is itself I/O-bound — calling out to an IDP,
querying a remote token introspection endpoint, fetching JWKs over HTTP —
you want the validation to happen on the event loop, not in a thread.
Declare the backend's methods `async def` and the async dispatcher awaits
them directly.

!!! danger "An async backend belongs to `server.async_urls` only"
    Only the async transport can await `authenticate`. The sync transport has
    no event loop to await on, so mounting an async backend under
    `server.urls` is a configuration error — it is refused with
    `ImproperlyConfigured` rather than served. See
    [What about the sync transport?](#what-about-the-sync-transport) below for
    why refusing is the only safe answer.

```python
import httpx
from django.contrib.auth import get_user_model
from django.http import HttpRequest

from rest_framework_mcp import MCPAuthBackend, TokenInfo
from rest_framework_mcp.auth.types.protected_resource_metadata import (
    ProtectedResourceMetadata,
)


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

        response = await self._client.post(self._url, data={"token": token}, auth=self._auth)
        if response.status_code != 200:
            return None
        claims = response.json()
        if not claims.get("active"):
            return None

        # Resolve the subject to a real user object. Session and task ownership
        # are keyed on `token.user.pk`; a bare `sub` string has no `pk`, so
        # every caller would collapse into the shared "anonymous" principal and
        # one caller's session id would be usable by another.
        user = await get_user_model().objects.filter(username=claims["sub"]).afirst()
        if user is None:
            return None

        return TokenInfo(
            user=user,
            scopes=tuple(claims.get("scope", "").split()),
            audience=claims.get("aud"),
            raw=claims,
        )

    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        # A dataclass, not a dict — the PRM ViewSet calls `.to_dict()` on it.
        return ProtectedResourceMetadata(
            resource="https://example.com/mcp/",
            authorization_servers=["https://idp.example/"],
            bearer_methods_supported=["header"],
        )

    def authorization_server_metadata(self):
        # This backend consumes a remote authorization server, it doesn't host
        # one. `NotImplementedError` is the documented signal that lets
        # `rest_framework_mcp.contrib.oauth` skip the AS-side endpoints.
        raise NotImplementedError("Authorization is hosted by the IDP, not by this resource.")

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

Mount under `async_urls` — and only `async_urls` — so the backend's
`authenticate` is awaited directly instead of being wrapped in
`sync_to_async`:

```python
urlpatterns = [path("mcp/", server.async_urls)]
```

## What about the sync transport?

**There is no bridge, and the same backend does not work under
`server.urls`.** The sync mount is a plain synchronous Django view with no
event loop, so calling an `async def authenticate` there returns an
un-awaited coroutine object rather than a `TokenInfo`. A coroutine is
*truthy*: the `token is None` check that produces the 401 would pass, and
every caller — credentials or not — would be authenticated. So the sync
transport inspects what `authenticate` returned and raises
`ImproperlyConfigured` naming both ways out, rather than serving a request
nobody authenticated.

That leaves two supported shapes:

- **ASGI** — keep the backend async and mount `server.async_urls`.
- **WSGI** — write the backend synchronously (`httpx.Client`, no `await`)
  and mount `server.urls`. Each request blocks one worker thread on the IDP
  round-trip, which is fine for low-throughput admin tools.

A sync backend works under *both* mounts: the async transport wraps sync
collaborators in `sync_to_async` at the call site. The asymmetry runs one
way only. So if your project mounts `server.urls` and `server.async_urls`
side by side, the backend has to be the sync one.

## Cleanup

`httpx.AsyncClient` holds a connection pool. If your process is going to
shut down cleanly (rare for Django), expose an `aclose()` from the backend
and call it from your ASGI lifespan handler. For typical deployments the
pool is freed when the process exits.
