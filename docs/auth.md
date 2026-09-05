# Authentication

`djangorestframework-mcp-server` is a **resource server**: it validates bearer tokens
that someone else issued. The library does not implement a token issuer (that's
the job of an Authorization Server / IDP). It does ship two backends and the
RFC 9728 metadata endpoint clients use to discover them.

!!! tip "OAuth contrib mount"
    The core `MCPServer.urls` exposes only the spec-mandated PRM endpoint.
    For deployments that want the full discovery + DCR matrix without
    fronting their own AS, `rest_framework_mcp.contrib.oauth.build_oauth_urlpatterns`
    bundles RFC 8414 AS metadata, OIDC discovery, RFC 7591 Dynamic Client
    Registration, and the alias paths different LLM hosts probe. See
    [OAuth contrib mount](#oauth-contrib-mount) below.

## The pieces

| Surface | Protocol | Default |
| --- | --- | --- |
| `MCPAuthBackend` | Authenticate request → `TokenInfo`, build `WWW-Authenticate`, supply PRM payload | **always** `DjangoOAuthToolkitBackend` unless you pass `auth_backend=`; mounting refuses if its extra is missing |
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

Pass it when you build the server:

```python
from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend

server = MCPServer(name="dev", auth_backend=AllowAnyBackend())
```

## `DjangoOAuthToolkitBackend`

Wraps [django-oauth-toolkit](https://django-oauth-toolkit.readthedocs.io/) as a
resource server. Bearer tokens are validated against DOT's `AccessToken` model;
scopes are projected into the `TokenInfo`. The package import is **lazy** — the
backend module imports cleanly even without `oauth2_provider` installed, so a
server used purely in process (`call_tool`, `list_tools`) needs no DOT at all.

Mounting is different, because from there on every request reaches
`authenticate()`. `server.urls` and `server.async_urls` ask the backend to check
itself first, so a missing extra is an `ImproperlyConfigured` while the URLConf
is imported — which `manage.py check` reaches — rather than a 500 on the first
request. Backends opt into that by implementing `check_configuration()`; one
that needs no setup implements nothing and is left alone.

```bash
pip install "djangorestframework-mcp-server[oauth]"
```

```python
INSTALLED_APPS = [
    # ...
    "oauth2_provider",
]

# DjangoOAuthToolkitBackend is the default — a server with no auth_backend=
# gets one. Pass it explicitly only to configure it.
REST_FRAMEWORK_MCP = {
    "SERVER_INFO": {
        "resource": "https://example.com/mcp/",
        "authorization_servers": ["https://example.com/oauth/"],
        "scopes_supported": ["invoices:read", "invoices:write"],
        "resource_metadata_url": "https://example.com/mcp/.well-known/oauth-protected-resource",
        "documentation": "https://example.com/docs/mcp/",
    },
}
```

The `SERVER_INFO` keys flow into both:

- `protected_resource_metadata()` — what the PRM endpoint returns.
- `www_authenticate_challenge()` — built from `resource_metadata_url`,
  any required scopes, and the `error="invalid_token"` code on auth failure.

!!! note "`name` and `version` belong on the server"
    `SERVER_INFO["name"]` / `["version"]` are the **fallback** for a server
    built without `name=` / `version=`. Prefer the constructor — it is the only
    way to give two servers in one project distinct identities:

    ```python
    MCPServer(name="internal", version="2.0.0", url_namespace="internal-mcp")
    ```

## `MCPPermission` classes

!!! warning "DRF viewset permissions do not apply over MCP"
    This package deliberately bypasses DRF's view-layer pipeline, so
    viewset-level `permission_classes` and the `REST_FRAMEWORK` default
    permission classes have **no effect** on MCP tool calls. Only
    `spec.permission_classes` (wrapped via `DRFPermissionAdapter`) and the
    per-binding `permissions=[...]` below gate a tool. Registering a tool
    with neither is **refused** — `ImproperlyConfigured` at registration.
    Set `REST_FRAMEWORK_MCP["REQUIRE_TOOL_PERMISSIONS"] = False` to downgrade
    that to an `UnguardedToolWarning` while migrating a large surface.

    The same check runs on `register_resource` and `register_prompt`: the
    identical selector reaches the identical rows whichever surface exposes
    it. Interactive **views** (`register_ui_resource`) are the one exemption,
    and a deliberate one — a view is a template rendered with no context, a
    literal document, or a zero-argument callable, none of which can read the
    caller's data.

    That third source is caller-blind **because registration enforces it**: a
    `selector=` declaring any fillable parameter is refused, since the read path
    resolves every binding's selector against a pool carrying `request` and
    `user`. Without that refusal the exemption was unsound for one of its three
    cases — a caller-aware view registered unguarded, served into a document
    hosts may cache across callers. If you want a view whose content depends on
    who is asking, you want `register_resource`, where the declaration check
    applies.

Per-binding permissions are AND-combined. Two ship in v1:

- `ScopeRequired(["a", "b"])` — token must carry every listed OAuth scope. A
  single scope may be passed bare: `ScopeRequired("invoices:write")`.
- `DjangoPermRequired("app.codename")` — `user.has_perm(...)` must be true. Anonymous users are always rejected by this class.

Both refuse an **empty** requirement. `all(...)` over nothing is `True`, so
`ScopeRequired([])` would permit every request while reading as a guard at the
registration site — and would satisfy the unguarded-tool check that would
otherwise have warned.

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
denial — return `[]` if there's nothing scope-shaped to advertise, or omit the
method entirely.

!!! warning "`permissions=` must contain permission objects"

    Every entry needs a `has_permission(request, token)` method, and this is
    checked at registration. The reason is not tidiness: an entry that cannot
    answer it is skipped at dispatch, so a binding "guarded" by one is
    **ungated** — and because the list is non-empty, the unguarded-tool warning
    stays quiet. `permissions="ScopeRequired"` is the likely way in: a bare
    string spreads into one entry per character.

### Reusing DRF `BasePermission` classes

If a permission class already exists as a DRF `BasePermission` (e.g. one
shared with your HTTP transport), wrap it with `DRFPermissionAdapter`
rather than rewriting it for MCP:

```python
from rest_framework.permissions import DjangoModelPermissions
from rest_framework_mcp import DRFPermissionAdapter

server.register_service_tool(
    name="invoices.create",
    spec=ServiceSpec(service=create_invoice),
    permissions=[DRFPermissionAdapter(DjangoModelPermissions)],
)
```

`ServiceSpec` / `SelectorSpec` also carry a `permission_classes`
attribute. Any DRF permission classes declared on the spec are
auto-wrapped and prepended to the per-binding `permissions` tuple —
the same spec that backs your HTTP view governs the MCP binding
without you restating the contract at the MCP call site.

### Object-level permissions

`has_object_permission` runs on every path, against the row the dispatch
resolved: the tool paths pass `enforce_permissions` to `dispatch_spec` as
`on_target_resolved`, `resources/read` runs it on the selector's return, and a
chain step runs it on the target the step resolved. A `LIST` / collection result
gets the class-level check only — object permissions are a per-row concept, and
a set is authorized per-set.

### Filtering listings by permissions

By default `tools/list`, `resources/list`, `resources/templates/list`,
and `prompts/list` return every registered binding regardless of whether
the current caller could invoke it. Set
`REST_FRAMEWORK_MCP["FILTER_LISTINGS_BY_PERMISSIONS"] = True` to drop
bindings whose permissions deny the caller before paginating.

This is **binding-level** gating — permissions are evaluated against a
synthetic data-less request, so a permission whose decision depends on
the call arguments will conservatively deny at list time. Mark such a
binding with `always_listed=True` to keep it visible as a discovery aid;
the permission still gates the actual invocation. Custom permissions can
declare an `is_listable(token)` method to override the list-time check
independently of `has_permission(request, token)`.

## Audience binding (RFC 8707)

`resource_url` is the identity this server **publishes** — RFC 9728 requires it
in protected-resource metadata, and it is what the `WWW-Authenticate` challenge
points clients at. Setting it does **not**, on its own, reject anything.

!!! warning "Enforcement is a separate, opt-in knob — and you probably want it on"
    Audience enforcement needs the access token to record which resource it was
    issued for.

    **django-oauth-toolkit 3.4.0 (2026-07-23) added RFC 8707 resource
    indicators**: stock `AccessToken` carries a `resource` field and an
    `allows_audience()` check, and `resource` is accepted at both the authorize
    and token endpoints. On 3.4.0 or later, `ENFORCE_AUDIENCE = True` needs
    nothing else.

    It is **off by default** only because the `[oauth]` extra floors DOT at
    `>=2.3`, and on an older DOT no token records a resource — a default of
    `True` would reject every request for anyone who has not upgraded. The check
    is capability-based rather than version-based, so it asks your configured
    token model rather than DOT's version number.

    Enforcement used to be implied by `resource_url` alone. That made the
    bundled backend unusable: configuring the resource URL a resource server is
    supposed to publish rejected *every* token, and clearing it published
    invalid metadata — there was no configuration that did both. Enforcement is
    now `ENFORCE_AUDIENCE`, default `False`.

!!! danger "The MCP spec makes this a MUST, so treat the default as a floor, not a recommendation"
    `2026-07-28` says a resource server **MUST** validate that access tokens were
    issued specifically for it. Leaving `ENFORCE_AUDIENCE` off on DOT 3.4.0+ is a
    conformance gap, and the failure mode is cross-resource token replay: a token
    minted for a different resource on the same authorization server is accepted
    here.

    Turn it on unless you are pinned below DOT 3.4.0.

To enforce, tell the backend where the audience actually lives — either a
swapped `OAUTH2_PROVIDER["ACCESS_TOKEN_MODEL"]` carrying a `resource` field, or
an explicit `audience_getter`:

```python
MCPServer(
    name="internal-mcp",
    auth_backend=DjangoOAuthToolkitBackend(
        resource_url="https://example.com/internal/mcp/",
        enforce_audience=True,
        # Wherever the resource really arrives: a JWT claim, a gateway header,
        # a related row. Returning None rejects the token.
        audience_getter=lambda token: token.jwt_claims.get("aud"),
    ),
)
```

Turning enforcement on without one of those raises `ImproperlyConfigured` at
startup, naming both ways out — a server that rejects everything is a
configuration error, and the only useful place to say so is where the
configuration is read, not in a 401 per request.

Because enforcement needs a getter, it means bringing your own backend:
`MCPServer(resource_url=...)` configures the default backend for *metadata*.

```python
REST_FRAMEWORK_MCP = {
    "RESOURCE_URL": "https://example.com/mcp/",
    "SERVER_INFO": {
        "authorization_servers": ["https://example.com/oauth/"],
        "scopes_supported": ["invoices:read", "invoices:write"],
        "resource_metadata_url": "https://example.com/mcp/.well-known/oauth-protected-resource",
    },
}
```

`RESOURCE_URL` is the **default** for a server that doesn't name its own. Since
RFC 8707 binds a token to *a* resource, each server needs its own canonical URL
— that binding is exactly what stops a token issued for one resource being
replayed against another, and two servers sharing one URL defeat it:

```python
# urls.py
internal = MCPServer(
    name="internal-mcp",
    resource_url="https://example.com/internal/mcp/",
    url_namespace="internal-mcp",
)
public = MCPServer(
    name="public-mcp",
    resource_url="https://example.com/public/mcp/",
    url_namespace="public-mcp",
)

urlpatterns = [
    path("internal/mcp/", internal.urls),
    path("public/mcp/", public.urls),
]
```

A token minted for `public-mcp` is now rejected by `internal-mcp`. Each server's
`WWW-Authenticate` challenge also points at **its own** PRM endpoint, derived
from its `resource_url` — so discovery lands on the right metadata.

`resource_url=` configures the default backend. If you bring your own
`auth_backend=`, it owns its audience binding — configure it there
(`DjangoOAuthToolkitBackend(resource_url=...)`); passing both raises.

`RESOURCE_URL` is also what the PRM endpoint advertises as `resource`, so one
value drives both "what we tell clients to ask for" and — when `ENFORCE_AUDIENCE`
is on — "what we accept". Leaving it unset publishes an empty `resource`, which
RFC 9728 marks REQUIRED, so the metadata carries a `_warning` saying why.

!!! note "Why exact-match"
    Token audiences are URLs, not patterns. Substring matches and prefix
    matches are unsafe (a token bound to `…/mcp` would otherwise satisfy a
    server expecting `…/mcp-admin`). The implementation enforces equality only.

### If you are writing a resource server against the official `mcp` SDK

!!! danger "The SDK publishes the metadata for you and never validates the audience"
    This is the single easiest thing to get wrong in this area, and it is not
    specific to this package — it is worth knowing wherever you build an MCP
    resource server in Python.

    `mcp.shared.auth_utils` provides `resource_url_from_server_url()` and
    `check_resource_allowed()`, and in the shipped wheel they are called **only
    from client paths**. `AccessToken` carries a `resource` field, but
    `BearerAuthBackend.authenticate()` checks the bearer prefix, the verifier's
    verdict and `expires_at` — and nothing else.

    So a `TokenVerifier` that merely checks a signature is **non-compliant with
    the audience MUST, and cross-resource token replay works**: a token minted
    for another resource on the same authorization server is accepted. FastMCP's
    `JWTVerifier` does validate audience out of the box; the official SDK's
    verifiers do not.

    If you are using this package's `DjangoOAuthToolkitBackend`, that is what
    `ENFORCE_AUDIENCE` is for and the check runs server-side. If you are writing
    your own verifier anywhere, validate the audience in it.

## OAuth contrib mount

`rest_framework_mcp.contrib.oauth.build_oauth_urlpatterns(*, server,
include_dcr=False, include_aliases=True, include_openid_discovery=True)`
returns URL patterns ready to mount alongside your server. It exposes the
full set of discovery endpoints LLM hosts probe so MCP clients (Claude
Desktop, Inspector, the various MCP-aware editors) can walk the auth
flow without you running a separate AS-facing service:

| Endpoint | Source |
| --- | --- |
| `/.well-known/oauth-authorization-server` | RFC 8414 AS metadata |
| `/.well-known/openid-configuration` | OIDC discovery (alias / minimal payload) |
| `/oauth/register/` (and aliases) | RFC 7591 Dynamic Client Registration |
| `/oauth/authorize/` | DOT's `AuthorizationView` (proxied so the user-adapter hook runs) |

Aliases render the canonical payload — they are not HTTP redirects.

```python title="urls.py"
from django.urls import path

from invoices.mcp import server
from rest_framework_mcp.contrib.oauth import build_oauth_urlpatterns

urlpatterns = [
    *build_oauth_urlpatterns(server=server, include_dcr=True),
    path("mcp/", server.urls),
]
```

DCR is gated by two knobs — defaults are deliberately conservative so an
accidental mount doesn't auto-register clients:

```python
(
    *build_oauth_urlpatterns(
        server=server,
        include_dcr=True,
        dcr_enabled=True,
        dcr_initial_access_token="share-this-with-trusted-clients",  # optional
    ),
)
```

When `dcr_enabled` is `False` the DCR endpoint refuses every request. When
`dcr_initial_access_token` is set, POST requests must present it as a bearer —
per RFC 7591 §3.

Both are resolved when the patterns are built. Omit either to take
`REST_FRAMEWORK_MCP["DCR_ENABLED"]` / `["DCR_INITIAL_ACCESS_TOKEN"]` as the
default; pass them to let two mounts in one project gate DCR differently.

### What a registration accepts and returns

The endpoint speaks RFC 7591's vocabulary. `token_endpoint_auth_method` decides
whether the client is public or confidential, and `grant_types` decides the
grant — the two fields an interoperable client actually sends:

```json title="POST /oauth/register/"
{
  "client_name": "Claude",
  "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
```

`token_endpoint_auth_method: none` registers a **public** client: no secret is
issued, and it authenticates at the token endpoint with PKCE alone. This is the
only mode Claude's custom connectors can use — that flow has no way to be handed
a pre-provisioned `client_id`, so DCR is its only path in. `client_secret_basic`
(the RFC's default when the field is omitted) and `client_secret_post` register
a **confidential** client and return a `client_secret`.

The registration above — a public client — comes back with no secret at all:

```json title="201 Created"
{
  "client_id": "…",
  "client_id_issued_at": 1753747200,
  "client_name": "Claude",
  "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none",
  "client_type": "public",
  "authorization_grant_type": "authorization-code"
}
```

A confidential registration adds `client_secret` and `client_secret_expires_at`
(`0`, meaning it does not expire). That secret is the plaintext, returned once
and never recoverable afterwards — DOT hashes the stored column, so nothing can
re-derive it.

Every field in the response is the **resolved** value — what was actually
registered — not an echo of what was asked for. RFC 7591 §3.2.1 lets an
authorization server substitute any metadata value it likes, but obliges it to
report what it settled on; a substitution the client is never told about is what
turns a legal downgrade into an undiagnosable failure at the token endpoint.

DOT's own `client_type` / `authorization_grant_type` spellings are still
accepted as an escape hatch for callers that already speak DOT, and are echoed
back alongside the RFC fields. Sending both vocabularies is fine when they
agree; a contradiction (`token_endpoint_auth_method: none` with
`client_type: confidential`) is a `400 invalid_client_metadata` rather than a
silent winner. DOT models one grant per application, so `grant_types` may name
at most one primary grant — `refresh_token` rides along and is not counted.

### `authorization_code` is the only grant you can register here

A dynamically registered client has **no owning user**: the row is created by an
unauthenticated caller, with no consent screen and no review step. So the grant
it may hold has to be one that cannot mint a token without a user at the other
end, which leaves `authorization_code` (plus `refresh_token` riding along).
`client_credentials`, `password` and `implicit` are refused with a
`400 invalid_client_metadata`, in either vocabulary.

`client_credentials` is the one that matters. Its token carries no user at all,
and `ScopeRequired` tests only the token's scopes — so a token minted from a
dynamically registered client-credentials application satisfies every
scope-gated tool. With `DCR_ENABLED = True` and no initial access token (the
default), that path starts at an unauthenticated `POST /oauth/register/`.
`password` and `implicit` are removed from OAuth 2.1 outright.
`grant_types_supported` in the metadata document never advertised any of the
three.

Machine-to-machine clients still belong in your deployment — create them through
Django admin or a management command, where an owner and a review step exist.

`response_types` is derived from the grant per RFC 7591 §2.1 rather than chosen
independently: `authorization_code` → `["code"]`. Supply it to assert the same
thing and it is accepted; supply something inconsistent and you get a `400`.

### Redirect URIs

`redirect_uris` must be absolute URIs, on a scheme the authorization server will
actually redirect to — which is DOT's own
`OAUTH2_PROVIDER["ALLOWED_REDIRECT_URI_SCHEMES"]`, `["http", "https"]` by
default. A native client registering an RFC 8252 §7.1 private-use scheme
(`com.example.app:/oauth2redirect`) needs that setting widened; it is the same
list the authorization request is checked against, so a registration is refused
only where the flow would have failed anyway.

### ID tokens and `openid`

If your DOT deployment sets `OIDC_ENABLED`, `openid` appears in `scopes_supported`
and clients will request it. The exchange then routes through the OpenID grant and
DOT tries to sign an ID token with the registered client's `algorithm`.

Registration resolves that algorithm rather than leaving it unset:

- `id_token_signed_response_alg: "RS256"` — honoured when
  `OAUTH2_PROVIDER["OIDC_RSA_PRIVATE_KEY"]` is configured, and a `400` naming that
  setting when it isn't.
- **Omitted** — takes RS256 when the server has a key, and otherwise registers no
  algorithm at all, which is the right outcome for a deployment not doing OIDC.
- `id_token_signed_response_alg: "HS256"` — always refused. HS256 signs the ID
  token with `client_secret`, and this endpoint leaves DOT's `hash_client_secret`
  at its default, so the stored column holds a PBKDF2 digest rather than the
  secret the client was handed. Signing with the digest produces a token whose
  signature can never verify.
- OIDC's `none` (an unsigned ID token) is not offered — DOT cannot mint one.

`/.well-known/openid-configuration` reports
`id_token_signing_alg_values_supported` derived from the same key, so it is empty
on a server that cannot sign. **If you want ID tokens, configure
`OIDC_RSA_PRIVATE_KEY`** — without it, clients that request `openid` register
successfully and get no ID token.

`scope` is checked against DOT's own scopes backend — the same set
`validate_scopes` uses at authorize time, which by default is
`OAUTH2_PROVIDER["SCOPES"]`. A registration naming a scope the server doesn't
offer is a `400` with the offending values named, rather than a `201` followed
by an `invalid_scope` a leg later with nothing linking the two. DOT stores no
per-application scope, so the registration can only ever be checked against the
global set, never narrowed to this client.

RFC 7591 fields the server doesn't understand (`contacts`, `logo_uri`, `jwks`, …)
are ignored, which §2 requires.

The contrib mount also surfaces AS metadata, so `AllowAnyBackend`
deployments (which have no AS) return `501 Not Implemented` on the AS
metadata endpoints rather than serving a fake payload. Use
`DjangoOAuthToolkitBackend` (or another backend that implements
`authorization_server_metadata()`) in production.

## The documented deployment: django-oauth-toolkit with CIMD

This is the configuration this project recommends and tests against. Everything
above is a seam you can replace; this section is the answer if you do not want
to make those choices yourself.

DOT acts as the Authorization Server as well as backing the resource server. The
MCP package only consumes the tokens it issues — the authorization endpoint,
token endpoint, refresh flow and client registration are all DOT's. Modern MCP
clients (Claude Desktop, Inspector) discover them through PRM → AS metadata.

Two settings carry most of the weight, and they are the reason this deployment
is the recommended one rather than one option among several:

- `ENFORCE_AUDIENCE` makes the RFC 8707 audience check mandatory, which the MCP
  specification requires of a resource server. DOT 3.4.0 and later record the
  `resource` on the token, so this works with the stock model.
- `CIMD_ENABLED` turns on Client ID Metadata Documents, which is how recent MCP
  clients register. It replaces Dynamic Client Registration, which the
  specification deprecates. See [Client ID Metadata
  Documents](#client-id-metadata-documents) below for what it does and what to
  decide about it.

```python title="settings.py"
INSTALLED_APPS = [
    # ...
    "oauth2_provider",
]

OAUTH2_PROVIDER = {
    # DOT 3.4.0+ accepts an RFC 8707 `resource` at the authorize and token
    # endpoints and records it on the token, so audience binding needs nothing
    # here — turn on ENFORCE_AUDIENCE instead. See "Audience binding" above.
    "SCOPES": {
        "invoices:read": "Read invoices",
        "invoices:write": "Mutate invoices",
    },
    # Token lifetimes appropriate for an MCP session — short access tokens,
    # refresh on demand.
    "ACCESS_TOKEN_EXPIRE_SECONDS": 600,
    "REFRESH_TOKEN_EXPIRE_SECONDS": 60 * 60 * 24,
    # Client ID Metadata Documents: how recent MCP clients register, and the
    # replacement for the deprecated Dynamic Client Registration. DOT publishes
    # `client_id_metadata_document_supported` in its AS metadata from this
    # setting, and this package reads the same setting when it builds its own
    # AS metadata, so the two cannot disagree.
    "CIMD_ENABLED": True,
    # Registration over CIMD happens before anyone has authenticated, so DOT's
    # default permission is open: any URL that serves a valid document becomes
    # a client. That is the CIMD model working as intended and it is the right
    # default for a public server. Narrow it if this server's tools are not
    # meant for arbitrary clients — see "Deciding how open CIMD should be".
    "CIMD_REGISTRATION_PERMISSION_CLASSES": ("oauth2_provider.cimd.AllowAllCIMDPermission",),
}

REST_FRAMEWORK_MCP = {
    "RESOURCE_URL": "https://example.com/mcp/",
    "ALLOWED_ORIGINS": ["https://app.example.com"],
    # The specification requires a resource server to validate that a token was
    # issued for it. Off by default only because the floor is DOT >=2.3, where
    # no token records a resource; on 3.4.0+ this is the correct setting.
    "ENFORCE_AUDIENCE": True,
    "SERVER_INFO": {
        "authorization_servers": ["https://example.com/oauth/"],
        "scopes_supported": ["invoices:read", "invoices:write"],
        "resource_metadata_url": "https://example.com/mcp/.well-known/oauth-protected-resource",
    },
}
```

!!! warning "Install the extra"
    `DjangoOAuthToolkitBackend` is the default backend, and mounting refuses
    without DOT: `pip install "djangorestframework-mcp-server[oauth]"`. The
    refusal happens while the URLConf is imported, so `manage.py check` catches
    it before a request ever arrives.

```python title="urls.py"
from django.urls import include, path

from invoices.mcp import server

urlpatterns = [
    path("oauth/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    path("mcp/", server.urls),
]
```

Verify the AS publishes [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414)
metadata before debugging client-side issues — DOT supports this, but the URL
is configurable. From a shell:

```bash
curl https://example.com/oauth/.well-known/oauth-authorization-server | jq .
```

You should see at minimum `issuer`, `authorization_endpoint`, `token_endpoint`,
and `client_id_metadata_document_supported: true`. A `registration_endpoint` also
appears for clients that still use Dynamic Client Registration. The PRM endpoint
you serve points clients at this AS, so a missing or wrong URL here is the most
common cause of "Inspector can't authenticate" reports.

### What the round-trip looks like

1. Client hits `tools/call` without a token.
2. Server returns 401 with
   `WWW-Authenticate: Bearer resource_metadata="https://example.com/mcp/.well-known/oauth-protected-resource", error="invalid_token"`.
3. Client fetches that URL → reads `authorization_servers`.
4. Client fetches `<as>/.well-known/oauth-authorization-server` → reads
   `client_id_metadata_document_supported` (CIMD), falling back to
   `registration_endpoint` (the deprecated DCR) if it does not speak CIMD.
5. Client publishes a Metadata Document at its own `client_id` URL — or
   pre-registers — then walks the authorization-code flow with
   `resource=https://example.com/mcp/`, so the issued access token is
   audience-bound to this server.
6. Client retries `tools/call` with the bearer token; server validates the
   token, checks audience, dispatches.

## Client ID Metadata Documents

Recent MCP clients prefer **Client ID Metadata Documents**
([draft-ietf-oauth-client-id-metadata-document](https://datatracker.ietf.org/doc/draft-ietf-oauth-client-id-metadata-document/))
over Dynamic Client Registration, which the MCP specification deprecates. A
client uses an **HTTPS URL as its `client_id`** and publishes a JSON metadata
document there; the authorization server fetches it. There is no registration
round-trip and no registration state to keep, and a client can rotate without
re-registering.

**DOT implements this natively from 3.4.0.** Set `CIMD_ENABLED = True` and DOT
advertises it in AS metadata, resolves URL-shaped client IDs, fetches and
validates the document, and provisions the `Application` row:

```json
{
  "issuer": "https://example.com/oauth/",
  "authorization_endpoint": "https://example.com/oauth/authorize/",
  "token_endpoint": "https://example.com/oauth/token/",
  "client_id_metadata_document_supported": true
}
```

This package reads the same `CIMD_ENABLED` setting when it builds its own AS
metadata, rather than carrying a flag of its own, so the two can never disagree
about what the deployment supports.

!!! danger "Do not hand-roll the fetch"
    Earlier versions of this page suggested fronting DOT with a small view that
    accepted a URL-shaped `client_id` and fetched it. That advice was wrong once
    DOT 3.4.0 shipped, and it was the dangerous kind of wrong: *fetch a URL the
    client just supplied* is a server-side request forgery unless it is
    carefully defended, and the defence is the hard part. DOT's fetcher
    validates the URL, resolves the hostname and rejects private and loopback
    addresses (so a DNS rebind does not slip through), caps the document size,
    the timeout, the number of concurrent fetches and the retry rate after a
    failure, and honours cache lifetimes. Use it.

### Deciding how open CIMD should be

CIMD registration happens on the pre-authorization path, where nobody has
authenticated yet, so DOT's default permission class is open: any URL serving a
valid document can become a client. That is the CIMD model working as designed —
the URL *is* the identity, and it is fetched rather than asserted — and it is
the right default for a server whose tools are meant for any client the user
authorizes.

It is the wrong default for a server whose tools are not. Narrow it with the
host allowlist:

```python title="settings.py"
OAUTH2_PROVIDER = {
    "CIMD_ENABLED": True,
    "CIMD_REGISTRATION_PERMISSION_CLASSES": ("oauth2_provider.cimd.HostAllowlistCIMDPermission",),
    # Django ALLOWED_HOSTS syntax: an exact host, or ".example.com" for a
    # domain and its subdomains. An empty list denies everything.
    "CIMD_ALLOWED_HOSTS": [".anthropic.com", "inspector.example.com"],
}
```

The remaining knobs — `CIMD_FETCH_TIMEOUT_SECONDS`, `CIMD_MAX_DOCUMENT_SIZE`,
`CIMD_METADATA_MIN_AGE_SECONDS` / `_MAX_AGE_SECONDS`,
`CIMD_FAILURE_BACKOFF_SECONDS`, `CIMD_MAX_CONCURRENT_FETCHES` — have sensible
defaults and are documented by DOT. `manage.py clearcimdapplications` prunes
rows for documents that have stopped resolving.

### What the resource server sees

Nothing. A CIMD-registered client's access token is byte-identical to a
pre-registered client's, so `authenticate()`, the audience check and the
permission classes all behave the same way. CIMD is an authorization-server
mechanism; it is worth turning on because it is what clients expect, not
because this package does anything with it beyond advertising it.

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
| Token accepted but every call still 401 | `ENFORCE_AUDIENCE` is on and the `audience_getter` returns something other than `RESOURCE_URL` (with stock DOT it returns `None`, since DOT records no resource). |
| 403 with `error="insufficient_scope"` and `scope=` in the challenge | Token authenticated, missing one of the per-binding scopes. The body also carries JSON-RPC `-32006` with `data.requiredScopes`. |
| 403 with no `scope=` | A non-scope permission denied (e.g. `DjangoPermRequired`). RFC 6750 defines no error code for that case, so the challenge advertises nothing rather than a scope the client cannot obtain. |
| 403 with `INVALID_REQUEST` (`-32600`) in the body | Not a permission failure — `Origin` rejection. |
