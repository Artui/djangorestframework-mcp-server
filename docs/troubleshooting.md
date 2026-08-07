# Troubleshooting

## Every tool call returns 404

This is the most confusing failure the transport can produce, because four
unrelated causes look identical from the client. Work down the list.

### 1. Read the `MCP-Error` header

Since 0.25.0 every session rejection carries one:

| Header | HTTP | Meaning |
|---|---|---|
| `MCP-Error: session-missing` | `400` | No `Mcp-Session-Id` arrived at all |
| `MCP-Error: session-unknown` | `404` | An id arrived that this server will not honour |
| *(no header)* | `404` | **Not us.** Something in front of the application answered |

That last row is the important one. A load balancer with no matching rule
returns a bodiless 404 that is indistinguishable from a dead session — and the
JSON-RPC body that would tell them apart often never reaches a human, because
clients commonly log `${status} ${statusText}` and **HTTP/2 has no reason
phrase**. If there is no `MCP-Error` header, look at your routing layer, not at
this package.

!!! warning "Application logs may be silent either way"

    If your logging does not include `rest_framework_mcp` (see
    [Observability](observability.md)), the absence of log lines tells you
    *nothing* about whether the request arrived. Configure the logger before
    concluding anything from silence.

### 2. Check whether the session simply expired

`session-unknown` covers expired, evicted, terminated, and minted-for-another-
principal. Sessions have two windows:

- `SESSION_TTL_SECONDS` — **idle** timeout, default 24h, restarted on every
  request. A connector used continuously never hits it.
- `SESSION_MAX_AGE_SECONDS` — **absolute** ceiling, default 7 days, regardless
  of activity.

A connector idle over a long weekend hits the first with nothing wrong anywhere.

### 3. Rule out cache eviction

Neither window can promise more than the cache underneath it. A Redis
`maxmemory-policy` of `allkeys-lru` (or `allkeys-random`) evicts session keys
long before their timeout, and that is **indistinguishable from expiry** from
every angle a client can observe.

```bash
redis-cli CONFIG GET maxmemory-policy
```

Anything other than `noeviction` or a `volatile-*` policy that respects TTLs
means raising the TTL will not help.

### 4. Check the client re-initializes

The spec is explicit: a client that receives 404 for a request carrying a
session id **MUST** start a new session by sending a fresh `InitializeRequest`.
A client that instead surfaces the 404 as a tool failure turns a recoverable
condition into an outage that needs a human. If yours does that, see below.

## Sessions keep breaking and you don't control the client

Turn them off:

```python title="settings.py"
REST_FRAMEWORK_MCP = {"SESSIONS_ENABLED": False}
```

This is a **conformant mode, not a relaxation.** Both legacy revisions say a
server *"MAY assign a session ID at initialization time"*, and make the client's
duty to echo one back conditional on it having arrived. A server that never
assigns is never sent one.

With it off, the `initialize`-handshake era runs statelessly: no id is minted,
none is required, and a client still echoing a stale id is ignored rather than
rejected — so flipping the setting does not itself cause the outage it prevents.

**What you give up:** server-initiated messaging on the legacy era. The session
id is what addresses a client's SSE channel, so the `GET` stream has no address
and answers `405`; the session `DELETE` does likewise. Request/response tool
calling is untouched.

**What it does not affect:** the modern (`2026-07-28`) era, which is stateless
already and ignores this setting entirely. If your client speaks it, none of
this section applies to you.

## `ImproperlyConfigured` on startup

### "registered with no permissions"

Since 0.25.0 a tool must declare permissions. DRF viewset-level and
`REST_FRAMEWORK` default permission classes **do not reach MCP** — this package
deliberately bypasses DRF's view pipeline — so a spec that looks guarded over
HTTP ships as an open tool.

```python
ServiceSpec(service=create_invoice, permission_classes=[IsAuthenticated])
```

To migrate a large surface gradually, downgrade it to a warning:

```python
REST_FRAMEWORK_MCP = {"REQUIRE_TOOL_PERMISSIONS": False}
```

!!! tip "If your tests assign the settings dict"

    `settings.REST_FRAMEWORK_MCP = {...}` **replaces** the dict rather than
    merging, so a project-level opt-out disappears inside any test that does
    that. Add the key to those literals too.

### "outputSchema would be advertised but structuredContent is disabled"

The spec requires a tool declaring an `outputSchema` to return conforming
`structuredContent`. Since 0.25.0 this is caught when the tool is registered
rather than on the first call.

Note that server-wide `INCLUDE_OUTPUT_SCHEMA=True` with
`INCLUDE_STRUCTURED_CONTENT=False` is **legal** — it just requires every binding
to override the content back on. The error names the binding that did not.

## OAuth discovery returns 404s

### The authorization server must be a site root

Endpoint paths are appended to it, so passing the value django-oauth-toolkit
advertises as *its* issuer (`https://host/oauth`) publishes
`https://host/oauth/oauth/authorize/` and two siblings like it. Pass the site
root. Since 0.25.0 this warns at construction; if you genuinely mount elsewhere,
set the paths instead:

```python
DjangoOAuthToolkitBackend(
    authorization_servers=["https://host"],
    authorize_path="/custom/authorize/",
)
```

### Mount order against `oauth2_provider`

django-oauth-toolkit 3.4.0 serves its own `register/` and
`.well-known/oauth-authorization-server`. Django resolves first-match, so
mounting DOT's urls **before** `build_oauth_urlpatterns(...)` means DOT answers
those paths — silently, with different content.

```python
def test_our_oauth_routes_are_not_shadowed():
    from rest_framework_mcp.contrib.oauth import check_oauth_url_shadowing

    assert check_oauth_url_shadowing() == []
```

It is a function you call rather than a Django system check because this package
is a library with no `AppConfig` — there is nowhere to register one.

## A tool result is too large for the client

`MAX_RESULT_BYTES` (default 5 MiB) bounds the wire payload, but a client's
context window is far smaller — a result well under the ceiling can still be
undeliverable. The bound now logs at `WARNING` when it fires.

If a paginated tool still returns too much, the row count is not the problem:
check how wide each row is. A nested serializer that expands related objects can
make ten rows larger than a thousand lean ones, and `limit` cannot express a
byte budget.
