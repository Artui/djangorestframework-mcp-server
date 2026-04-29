# Authentication

`djangorestframework-mcp-server` is a **resource server**: it validates bearer tokens
that someone else issued. The library does not implement a token issuer (that's
the job of an Authorization Server / IDP). It does ship two backends and the
RFC 9728 metadata endpoint clients use to discover them.

!!! tip "v1 scope"
    The library does not implement Dynamic Client Registration (DCR) itself.
    The two recipes below show how to make DCR or Client ID Metadata Documents
    work with your existing Authorization Server — both spec-compliant and
    sufficient for modern MCP clients.

## The pieces

| Surface | Protocol | Default |
| --- | --- | --- |
| `MCPAuthBackend` | Authenticate request → `TokenInfo`, build `WWW-Authenticate`, supply PRM payload | `DjangoOAuthToolkitBackend` if `oauth2_provider` is installed, else configurable |
| `MCPPermission` | Per-tool / per-resource gate (AND-combined) | `[]` (no extra constraints) |
| `/.well-known/oauth-protected-resource` | RFC 9728 metadata | served from backend's `protected_resource_metadata()` |

The transport flow on every request:

1. Validate `Origin`, `MCP-Protocol-Version`, `MCP-Session-Id` (where required).
2. `backend.authenticate(request)` → `TokenInfo | None`. `None` → 401 with the
   challenge from `backend.www_authenticate_challenge(...)`.
3. Per-binding permissions evaluated; denial → 403, required scopes surfaced in
   the challenge.
4. Handler dispatched.

## `AllowAnyBackend` (dev only)

Authenticates every request as anonymous. The metadata payload is intentionally
minimal and includes a `_warning`. Don't ship this to production.

```python
REST_FRAMEWORK_MCP = {
    "AUTH_BACKEND":
        "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
}
```

Or pass it explicitly when you build the server (useful in tests):

```python
from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend

server = MCPServer(name="dev", auth_backend=AllowAnyBackend())
```

## `DjangoOAuthToolkitBackend`

Wraps [django-oauth-toolkit](https://django-oauth-toolkit.readthedocs.io/) as a
resource server. Bearer tokens are validated against DOT's `AccessToken` model;
scopes are projected into the `TokenInfo`. The package import is **lazy** — the
backend module imports cleanly even without `oauth2_provider` installed; the
`ImportError` only fires when `authenticate()` actually runs.

```bash
pip install "djangorestframework-mcp-server[oauth]"
```

```python
INSTALLED_APPS = [
    # ...
    "oauth2_provider",
]

REST_FRAMEWORK_MCP = {
    "AUTH_BACKEND":
        "rest_framework_mcp.auth.backends.django_oauth_toolkit_backend"
        ".DjangoOAuthToolkitBackend",
    "SERVER_INFO": {
        "name": "my-app",
        "resource": "https://example.com/mcp/",
        "authorization_servers": ["https://example.com/oauth/"],
        "scopes_supported": ["invoices:read", "invoices:write"],
        "resource_metadata_url":
            "https://example.com/mcp/.well-known/oauth-protected-resource",
        "documentation": "https://example.com/docs/mcp/",
    },
}
```

The `SERVER_INFO` keys flow into both:

- `protected_resource_metadata()` — what the PRM endpoint returns.
- `www_authenticate_challenge()` — built from `resource_metadata_url`,
  any required scopes, and the `error="invalid_token"` code on auth failure.

## `MCPPermission` classes

Per-binding permissions are AND-combined. Two ship in v1:

- `ScopeRequired(["a", "b"])` — token must carry every listed OAuth scope.
- `DjangoPermRequired("app.codename")` — `user.has_perm(...)` must be true. Anonymous users are always rejected by this class.

```python
from rest_framework_mcp import MCPServer, ScopeRequired, DjangoPermRequired
from rest_framework_services.types.service_spec import ServiceSpec

server.register_service_tool(
    name="invoices.refund",
    spec=ServiceSpec(service=refund_invoice),
    permissions=[
        ScopeRequired(["invoices:write"]),
        DjangoPermRequired("invoices.refund_invoice"),
    ],
)
```

Custom permissions implement the [`MCPPermission`](reference/auth.md) Protocol:

```python
from django.http import HttpRequest
from rest_framework_mcp import TokenInfo


class TenantMatches:
    def __init__(self, tenant_id: int) -> None:
        self._tenant_id = tenant_id

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return getattr(token.user, "tenant_id", None) == self._tenant_id

    def required_scopes(self) -> list[str]:
        return []
```

`required_scopes()` is what gets surfaced in the `WWW-Authenticate` header on
denial — return `[]` if there's nothing scope-shaped to advertise.

## Audience binding (RFC 8707)

`DjangoOAuthToolkitBackend` enforces RFC 8707 audience binding when
`REST_FRAMEWORK_MCP["RESOURCE_URL"]` is configured. Every accepted token must
carry that URL as its bound resource; tokens with a missing or mismatched
`aud` / `resource` are rejected as if the bearer were absent.

```python
REST_FRAMEWORK_MCP = {
    "RESOURCE_URL": "https://example.com/mcp/",
    "AUTH_BACKEND":
        "rest_framework_mcp.auth.backends.django_oauth_toolkit_backend"
        ".DjangoOAuthToolkitBackend",
    "SERVER_INFO": {
        "authorization_servers": ["https://example.com/oauth/"],
        "scopes_supported": ["invoices:read", "invoices:write"],
        "resource_metadata_url":
            "https://example.com/mcp/.well-known/oauth-protected-resource",
    },
}
```

`RESOURCE_URL` is also what the PRM endpoint advertises as `resource`, so the
configuration cannot drift between "what we accept" and "what we tell clients
to ask for". Setting `RESOURCE_URL` to `None` (the default) disables
enforcement — appropriate for development or for deployments where audience
binding happens at an upstream gateway.

!!! note "Why exact-match"
    Token audiences are URLs, not patterns. Substring matches and prefix
    matches are unsafe (a token bound to `…/mcp` would otherwise satisfy a
    server expecting `…/mcp-admin`). The implementation enforces equality only.

## Recipe: bring-your-own AS via django-oauth-toolkit

DOT can act as the Authorization Server too. The MCP package only consumes the
tokens it issues — DCR, the authorization endpoint, token endpoint, and refresh
flow are all handled by DOT itself. Modern MCP clients (Claude Desktop,
Inspector) discover them through PRM → AS metadata.

```python title="settings.py"
INSTALLED_APPS = [
    # ...
    "oauth2_provider",
]

OAUTH2_PROVIDER = {
    # Bind every issued token to the canonical resource URL so the resource
    # server can perform RFC 8707 audience checks.
    "REQUIRE_RESOURCE": True,
    "SCOPES": {
        "invoices:read":  "Read invoices",
        "invoices:write": "Mutate invoices",
    },
    # Token lifetimes appropriate for an MCP session — short access tokens,
    # refresh on demand.
    "ACCESS_TOKEN_EXPIRE_SECONDS": 600,
    "REFRESH_TOKEN_EXPIRE_SECONDS": 60 * 60 * 24,
}

REST_FRAMEWORK_MCP = {
    "RESOURCE_URL": "https://example.com/mcp/",
    "AUTH_BACKEND":
        "rest_framework_mcp.auth.backends.django_oauth_toolkit_backend"
        ".DjangoOAuthToolkitBackend",
    "ALLOWED_ORIGINS": ["https://app.example.com"],
    "SERVER_INFO": {
        "authorization_servers": ["https://example.com/oauth/"],
        "scopes_supported": ["invoices:read", "invoices:write"],
        "resource_metadata_url":
            "https://example.com/mcp/.well-known/oauth-protected-resource",
    },
}
```

```python title="urls.py"
from django.urls import include, path

from invoices.mcp import server

urlpatterns = [
    path("oauth/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    path("mcp/", include(server.urls)),
]
```

Verify the AS publishes [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414)
metadata before debugging client-side issues — DOT supports this, but the URL
is configurable. From a shell:

```bash
curl https://example.com/oauth/.well-known/oauth-authorization-server | jq .
```

You should see at minimum `issuer`, `authorization_endpoint`, `token_endpoint`,
and (for DCR-aware clients) `registration_endpoint`. The PRM endpoint you serve
points clients at this AS, so a missing or wrong URL here is the most common
cause of "Inspector can't authenticate" reports.

### What the round-trip looks like

1. Client hits `tools/call` without a token.
2. Server returns 401 with
   `WWW-Authenticate: Bearer resource_metadata="https://example.com/mcp/.well-known/oauth-protected-resource", error="invalid_token"`.
3. Client fetches that URL → reads `authorization_servers`.
4. Client fetches `<as>/.well-known/oauth-authorization-server` → reads
   `registration_endpoint` (DCR) or `client_id_metadata_document_supported`
   (CIMD).
5. Client either pre-registers or publishes a Metadata Document, then walks
   the authorization-code flow with `resource=https://example.com/mcp/` so the
   issued access token is audience-bound to this server.
6. Client retries `tools/call` with the bearer token; server validates the
   token, checks audience, dispatches.

## Recipe: Client ID Metadata Documents

Many recent MCP clients prefer
[Client ID Metadata Documents](https://datatracker.ietf.org/doc/draft-ietf-oauth-client-id-metadata-document/)
(CIMD) over DCR — they avoid an extra registration round-trip and let clients
rotate without server-side state. If your AS supports CIMD, advertise it in
the AS metadata response:

```json
{
  "issuer": "https://example.com/oauth/",
  "authorization_endpoint": "https://example.com/oauth/authorize/",
  "token_endpoint": "https://example.com/oauth/token/",
  "registration_endpoint": "https://example.com/oauth/register/",
  "client_id_metadata_document_supported": true
}
```

DOT does not implement CIMD natively today; the typical setup is to front it
with a small wrapper view that:

1. Accepts a `client_id` shaped like a URL.
2. Fetches that URL, validates the document against the
   [draft schema](https://datatracker.ietf.org/doc/draft-ietf-oauth-client-id-metadata-document/),
   and either resolves to an existing DOT `Application` row or provisions one
   on the fly with the document's `redirect_uris`.

From the resource server's perspective nothing changes — the access tokens
issued at the end of the flow look identical. The only requirement is that
the AS is forwarding the `resource` parameter through to the token, which DOT
handles when `REQUIRE_RESOURCE` is set.

## Try it with mcp-inspector

```bash
npx @modelcontextprotocol/inspector --url http://localhost:8000/mcp/
```

Inspector reads PRM, hits your AS metadata, walks the auth flow, and exercises
`tools/list` + `tools/call`. Common failure modes and where to look:

| Symptom | Likely cause |
| --- | --- |
| 401 with no `WWW-Authenticate` | Custom auth backend forgot to return a challenge. Check `www_authenticate_challenge`. |
| 401 with `WWW-Authenticate` but no `resource_metadata` | `SERVER_INFO["resource_metadata_url"]` not set. |
| Token accepted but every call still 401 | `RESOURCE_URL` set but the AS isn't binding `resource` to the token. |
| 403 with `scope=` in challenge | Token authenticated, missing one of the per-binding scopes. |
| 403 with no `scope=` | A non-scope permission denied (e.g. `DjangoPermRequired`). |
