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

The companion `cacheScope` is **derived, not configured**: a listing filtered by
`FILTER_LISTINGS_BY_PERMISSIONS` is `private`, an unfiltered one is `public`, and
a resource body is always `private`. `public` licenses a shared proxy to serve
one response across authorization contexts, which is not a preference — getting
it wrong is a cross-tenant disclosure with a cache in front of it.

!!! note "What `public` on a catalog does and doesn't say"

    The spec's criterion for `public` is that *"the response does not contain
    user-specific data"* — which an unfiltered catalog satisfies exactly: every
    caller gets byte-identical output, so the derivation is the spec's own test
    rather than a judgement call.

    It is worth being clear what follows, though. `public` is not a statement
    that the catalog is *non-confidential*. It permits a shared intermediary to
    serve a stored copy across authorization contexts, and a tool catalog does
    describe the server's capability surface. If that surface is something you
    would rather not have cached in front of an authenticated endpoint, turn on
    `FILTER_LISTINGS_BY_PERMISSIONS` — the result becomes per-caller and the
    derivation follows it to `private`.

| Key | Default | What it does |
|---|---|---|
| `PROTOCOL_VERSIONS` | `["2026-07-28", "2025-11-25", "2025-06-18"]` | Every revision this server speaks, most-preferred first, across **both eras** — `2026-07-28` is modern (per-request metadata, no session), the rest are legacy (`initialize` handshake). `server/discover` reports the whole list; each era validates against its own half. ⚠ `initialize` never offers a modern version whatever heads the list: the handshake does not exist there, so answering with it would hand the client a protocol the transport would refuse on its next request. |
| `REQUIRE_PROTOCOL_VERSION_HEADER` | `True` | Reject a post-`initialize` request that omits `MCP-Protocol-Version` (HTTP 400). Set `False` for clients that never send it. A header that is *present but unsupported* is rejected either way — downgrading silently there would mask a real version mismatch. |
| `SERVER_INFO` | `{"name": "djangorestframework-mcp-server"}` | Default `serverInfo` for `initialize`. Recognised keys: `name`, `version`, `title`, `description`, `websiteUrl`, `icons` (a list of `{src, mimeType, sizes, theme}` dicts). Prefer per-server identity: `MCPServer(name=…, version=…, title=…, website_url=…, icons=…)`. `description` is settings-only — the constructor's `description=` is the `initialize` `instructions` string, which is written for the model rather than for a connection list. |
| `CATALOG_CACHE_TTL_MS` | `60000` | How long a client may cache a catalog result — `server/discover` plus the four list methods — emitted as `ttlMs`. `0` means "immediately stale". A catalog is fixed once the process boots, so the honest ceiling is "until the next deploy", which nothing here can know; a minute costs a client one stale minute after a release rather than a stale catalog for the life of its connection. |
| `RESOURCE_CACHE_TTL_MS` | `0` | The same for `resources/read`. `0` by default because a resource body is whatever a selector just produced. A genuinely static resource — an interactive view, a rendered document — opts in per binding with `cache_ttl_ms=`. |
| `TASK_TTL_MS` | `86400000` (24 h) | How long a created task stays readable, reported to the client as `ttlMs`. Both a promise and a bound: after it elapses the record may be dropped, and a client still politely polling gets "unknown task" — which looks exactly like work that vanished, hence the generous default. `None` disables expiry, which is only sound for a store that evicts on its own (the cache-backed one falls back to a week so an un-polled task cannot pin memory). |
| `TASK_POLL_INTERVAL_MS` | `5000` | Suggested `tasks/get` cadence, sent as `pollIntervalMs`. Advisory — clients *SHOULD* honour it, and a server *MAY* rate-limit one that polls faster. Worth tuning to how long the work actually takes: too low and every task costs a stream of no-op polls, too high and a finished task sits while its client waits. `None` omits the hint. |

## Transport & security

| Key | Default | What it does |
|---|---|---|
| `ALLOWED_ORIGINS` | `[]` | Origin allowlist, enforced on every request (mandatory per the MCP spec). `["*"]` allows any origin — local development only. |
| `MAX_PROGRESS_NOTIFICATIONS` | `1000` | Ceiling on `notifications/progress` frames emitted for one request. The spec asks both parties to rate-limit progress, and the failure mode is the familiar one: a service reporting per row over a large table turns one call into a flood. Past the cap further reports are dropped — the dispatch is untouched and the final response still arrives. |
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

## Outbound bounds

The mirror of `MAX_REQUEST_BYTES` on the way out. All three accept `None` to
disable, and all three can be overridden per tool at registration
(`max_result_bytes=`, `dispatch_timeout=`, `max_page_size=`) — pass `None`
there to lift the bound for one deliberately-large or deliberately-slow tool.
See [What the package bounds](../performance.md#what-the-package-bounds).

| Key | Default | What it does |
|---|---|---|
| `MAX_RESULT_BYTES` | `5242880` (5 MiB) | Ceiling on one tool result or resource read, measured on the **encoded wire payload**. A successful tool result carries the payload twice — as `structuredContent` and as the spec's backwards-compatibility text mirror — so a ceiling counting one copy would be wrong by 2× against the thing that matters, the client's context window. Over the ceiling the call returns an `isError` result naming the remedy; **it is never truncated**, because a clipped list reads as complete to the model reasoning from it. |
| `MAX_PAGE_SIZE` | `500` | Ceiling on the model-supplied `limit` of a `paginate=True` selector tool. Advertised as `maximum` on the generated `inputSchema` *and* clamped at dispatch — the schema tells a well-behaved model what to ask for, the clamp is what stops you trusting it. Clamping is safe here because `totalPages` / `hasNext` keep a clamped page self-describing. |
| `DISPATCH_TIMEOUT` | `60.0` | Wall-clock ceiling, in seconds, on one dispatch. **ASGI only** — a sync WSGI view has no in-process way to bound its own dispatch. ⚠ It does **not** reclaim the worker: a thread parked in a database driver's socket read is not interruptible by asyncio cancellation, so the query runs on. What it buys is a *terminal protocol event* instead of an open request that never resolves. Pair it with a database statement timeout. |
| `REQUIRE_LIST_PAGINATION` | `False` | Registering a LIST selector tool with `paginate=False` raises `ImproperlyConfigured` instead of emitting `UnboundedListWarning`. Such a tool serialises whatever its selector resolves to — the whole table, for a plain `Model.objects.all()` — and unlike a paginated tool there is no honest way to clamp it, because the result carries no metadata saying rows were dropped. `MAX_RESULT_BYTES` is the backstop; `paginate=True` is the fix. |

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
