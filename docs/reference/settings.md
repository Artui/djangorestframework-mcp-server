# Settings

Every key lives in a single `REST_FRAMEWORK_MCP` dict in your Django settings.
All of them are optional — the defaults below apply when a key is absent.

```python
# settings.py
REST_FRAMEWORK_MCP = {
    "ALLOWED_ORIGINS": ["https://app.example.com"],
    "REQUIRE_TOOL_PERMISSIONS": True,
}
```

Reading an unknown key raises `KeyError`, so a typo surfaces immediately rather
than silently taking a default.

**These settings are per-project defaults.** Every scalar below can be
overridden per server, which is what you want as soon as you mount more than
one:

```python
# urls.py
server = MCPServer(name="internal", config=build_mcp_config(page_size=500))
```

Collaborators are **not** configured here at all — the auth backend, session
store, SSE broker, server identity and resource URL are passed to
`MCPServer(...)` in `urls.py`, because a settings dict can't hold a live
object. Three keys that once named a collaborator by dotted path were removed
and now raise `ImproperlyConfigured` if still present: `AUTH_BACKEND`,
`SESSION_STORE`, `AUTH_USER_ADAPTER`.

## Protocol

| Key | Default | What it does |
|---|---|---|
| `PROTOCOL_VERSIONS` | `["2025-11-25", "2025-06-18"]` | Accepted `MCP-Protocol-Version` values. The first entry is the fallback when the header is absent and not required. |
| `REQUIRE_PROTOCOL_VERSION_HEADER` | `True` | Reject a post-`initialize` request that omits `MCP-Protocol-Version` (HTTP 400). Set `False` for clients that never send it. A header that is *present but unsupported* is rejected either way — downgrading silently there would mask a real version mismatch. |
| `SERVER_INFO` | `{"name": "djangorestframework-mcp-server"}` | Default `serverInfo` for `initialize`. Prefer per-server identity: `MCPServer(name=…, version=…, title=…)`. |

## Transport & security

| Key | Default | What it does |
|---|---|---|
| `ALLOWED_ORIGINS` | `[]` | Origin allowlist, enforced on every request (mandatory per the MCP spec). `["*"]` allows any origin — local development only. |
| `MAX_REQUEST_BYTES` | `1048576` (1 MiB) | Maximum accepted request body size. |
| `RESOURCE_URL` | `None` | Canonical resource URL this server **publishes** — RFC 9728 requires it in protected-resource metadata, and it is what audience enforcement compares against when enabled. Setting it rejects nothing on its own; see `ENFORCE_AUDIENCE`. Only the **default** for `MCPServer(resource_url=…)` — RFC 8707 binds a token to *a* resource, so each server needs its own URL. Two servers sharing one URL means a token minted for one passes the audience check at the other, which is the exact replay the mechanism prevents. Leaving it unset publishes an empty `resource` plus a `_warning`. |
| `ENFORCE_AUDIENCE` | `False` | Whether a token whose bound resource doesn't equal `RESOURCE_URL` is **rejected**. Off by default because enforcement needs the access token to record its resource, and **DOT's stock `AccessToken` has no such field** — DOT implements no RFC 8707 resource indicators. Enforcement was once implied by `RESOURCE_URL` alone, so the bundled backend rejected every token as soon as a resource URL was configured. Turn it on with a swapped `OAUTH2_PROVIDER["ACCESS_TOKEN_MODEL"]` carrying a `resource` field, or `DjangoOAuthToolkitBackend(audience_getter=…)`; without either the backend raises `ImproperlyConfigured` at startup rather than 401-ing every request. See [Authentication](../auth.md#audience-binding-rfc-8707). |

## Tools & output

| Key | Default | What it does |
|---|---|---|
| `DEFAULT_OUTPUT_FORMAT` | `"json"` | Format of a tool result's human-readable `content[0]` text for tools that don't set one: `"json"`, `"toon"`, or `"auto"` (TOON for uniform lists, JSON otherwise). `structuredContent` is always JSON. Per-tool `output_format=` wins. See [Ship TOON for large lists](../recipes/toon-output.md). |
| `INCLUDE_STRUCTURED_CONTENT` | `True` | Emit `structuredContent` on tool results. |
| `INCLUDE_OUTPUT_SCHEMA` | `True` | Advertise `outputSchema` on tool definitions. |
| `INCLUDE_VALIDATION_VALUE` | `False` | Include the offending `arguments` dict under `data.value` in validation errors. Off by default — that dict can carry PII or secrets, which would then flow back to the client and into its logs. |
| `PAGE_SIZE` | `100` | Maximum items returned by one **listing** call (`tools/list`, `resources/list`, `resources/templates/list`, `prompts/list`). Clients page with the opaque `cursor` echoed in the response. |

!!! warning "One combination is a spec violation"
    `INCLUDE_STRUCTURED_CONTENT` and `INCLUDE_OUTPUT_SCHEMA` are independent,
    but advertising `outputSchema` while suppressing `structuredContent` is
    forbidden by the spec and raises `ImproperlyConfigured` at request time. If
    you turn `INCLUDE_STRUCTURED_CONTENT` off, turn `INCLUDE_OUTPUT_SCHEMA` off
    too (or set `include_output_schema=False` per binding). The other direction
    — `structuredContent` without `outputSchema` — is allowed.

## Permissions

| Key | Default | What it does |
|---|---|---|
| `REQUIRE_TOOL_PERMISSIONS` | `False` | Registering a tool with no permissions at all (neither `spec.permission_classes` nor `permissions=[…]`) raises `ImproperlyConfigured` instead of emitting `UnguardedToolWarning`. The warning exists because guarding the *viewset* — or relying on `REST_FRAMEWORK`'s default permission classes — has **no effect over MCP**: this package bypasses DRF's view-layer pipeline, so a spec that looks guarded over HTTP ships as an unguarded tool. See [Authentication](../auth.md). |
| `REQUIRE_TOOL_DESCRIPTIONS` | `False` | Registering a tool with no `description` raises `ImproperlyConfigured` instead of emitting `UndescribedToolWarning`. The description is the only thing a model reads to decide whether and how to call a tool, so an empty one ships a tool that cannot be used correctly — and `tools/list` renders it indistinguishably from a documented one. There is deliberately **no docstring fallback**: a docstring is written for the next developer, not for a model choosing between tools. See [Documenting tools](../concepts.md#documenting-tools). |
| `FILTER_LISTINGS_BY_PERMISSIONS` | `False` | Drop bindings whose `permissions` deny the caller from `tools/list`, `resources/list`, `resources/templates/list` and `prompts/list`. Per-binding `always_listed=True` opts one back in as a discovery aid. |

## OAuth

| Key | Default | What it does |
|---|---|---|
| `DCR_ENABLED` | `False` | Default for `build_oauth_urlpatterns(dcr_enabled=)`. RFC 7591 dynamic client registration; `False` makes `/oauth/register/` refuse every request with 403. An open DCR endpoint lets anyone create an OAuth client against your authorization server. |
| `DCR_INITIAL_ACCESS_TOKEN` | `None` | Default for `build_oauth_urlpatterns(dcr_initial_access_token=)`. The RFC 7591 §3 initial access token clients must present as `Authorization: Bearer …`. `None` means no token check — anyone who can reach the endpoint can register. |
| `SIMPLEJWT_ACCESS_COOKIE` | `"access"` | Default for `SimpleJWTCookieAdapter(cookie_name=)` (`[jwt]` extra) — the cookie it reads access tokens from. Matches `djangorestframework-simplejwt`'s documented `AUTH_COOKIE` default. |

## Observability

| Key | Default | What it does |
|---|---|---|
| `RECORD_SERVICE_EXCEPTIONS` | `False` | Record a `ServiceError` raised from a tool callable on the active OpenTelemetry span before mapping it to a JSON-RPC error. Off by default because services often raise `ServiceError` for routine business-rule denials, which would flood error pipelines. `ServiceValidationError` is *never* recorded — it is client input failure, not a server fault. See [Observability](../observability.md). |
