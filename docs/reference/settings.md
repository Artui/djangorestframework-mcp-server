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

!!! warning "Assigning the dict replaces it; it does not merge"
    Absent **keys** fall back to the defaults above, so a partial
    `REST_FRAMEWORK_MCP` is fine. But assigning the dict — including
    `settings.REST_FRAMEWORK_MCP = {...}` in a test, or `@override_settings` —
    replaces whatever was there, so a project-level opt-out silently disappears
    inside that scope. Repeat the keys you rely on in those literals.

    The same holds one level down: a **dict-valued** setting such as
    `SERVER_INFO` is taken whole, not deep-merged. Supplying
    `{"version": "1.0"}` drops any `name` you had configured alongside it (the
    package name is used as the fallback), so give the dict every key you want.

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
| `PROTOCOL_VERSIONS` | `["2026-07-28", "2025-11-25", "2025-06-18"]` | Every revision this server speaks, most-preferred first, across **both eras** — `2026-07-28` is modern (per-request metadata, no session), the rest are legacy (`initialize` handshake). `server/discover` reports the whole list; each era validates against its own half. `initialize` never offers a modern version whatever heads the list: the handshake does not exist there, so answering with it would hand the client a protocol the transport would refuse on its next request. |
| `REQUIRE_PROTOCOL_VERSION_HEADER` | `True` | Reject a post-`initialize` request that omits `MCP-Protocol-Version` (HTTP 400). Set `False` for clients that never send it. A header that is *present but unsupported* is rejected either way — downgrading silently there would mask a real version mismatch. |
| `SERVER_INFO` | `{"name": "djangorestframework-mcp-server"}` | Default `serverInfo` for `initialize`. Recognised keys: `name`, `version`, `title`, `description`, `websiteUrl`, `icons` (a list of `{src, mimeType, sizes, theme}` dicts). Prefer per-server identity: `MCPServer(name=…, version=…, title=…, website_url=…, icons=…)`. `description` is settings-only — the constructor's `description=` is the `initialize` `instructions` string, which is written for the model rather than for a connection list. |
| `CATALOG_CACHE_TTL_MS` | `60000` | How long a client may cache a catalog result — `server/discover` plus the four list methods — emitted as `ttlMs`. `0` means "immediately stale". A catalog is fixed once the process boots, so the honest ceiling is "until the next deploy", which nothing here can know; a minute costs a client one stale minute after a release rather than a stale catalog for the life of its connection. |
| `RESOURCE_CACHE_TTL_MS` | `0` | The same for `resources/read`. `0` by default because a resource body is whatever a selector just produced. A genuinely static resource — an interactive view, a rendered document — opts in per binding with `cache_ttl_ms=`. |
| `TASK_TTL_MS` | `86400000` (24 h) | How long a created task stays readable, reported to the client as `ttlMs`. Both a promise and a bound: after it elapses the record may be dropped, and a client still politely polling gets "unknown task" — which looks exactly like work that vanished, hence the generous default. `None` disables expiry, which is only sound for a store that evicts on its own (the cache-backed one falls back to a week so an un-polled task cannot pin memory). |
| `TASK_POLL_INTERVAL_MS` | `5000` | Suggested `tasks/get` cadence, sent as `pollIntervalMs`. Advisory — clients *SHOULD* honour it, and a server *MAY* rate-limit one that polls faster. Worth tuning to how long the work actually takes: too low and every task costs a stream of no-op polls, too high and a finished task sits while its client waits. `None` omits the hint. |
| `INPUT_REQUEST_TTL_SECONDS` | `600` (10 min) | How long a `requestState` handed to a client stays redeemable. Bounds the replay window on the one value in this protocol that leaves the server, passes through the client, and comes back trusted — the spec requires the expiry alongside the principal and originating-request checks, which are not configurable because there is no defensible value other than "enforced". Long enough to read a confirmation dialog and decide; short enough that a token captured from a log is dead before anyone replays it. |
| `MAX_INPUT_ROUNDS` | `5` | How many times one call may ask the user for something before failing instead of asking again. The spec explicitly allows asking repeatedly, and a service that wants a confirmation *and* a reason legitimately needs two rounds. What this bounds is the service whose condition the answer never clears — otherwise client and server volley the same question at a user indefinitely. Past the cap the call returns the service's own message as an `isError` result. |

## Transport & security

| Key | Default | What it does |
|---|---|---|
| `ALLOWED_ORIGINS` | `[]` | Origin allowlist, enforced on every request (mandatory per the MCP spec). `["*"]` allows any origin — local development only. |
| `MAX_PROGRESS_NOTIFICATIONS` | `1000` | Ceiling on `notifications/progress` frames emitted for one request. The spec asks both parties to rate-limit progress, and the failure mode is the familiar one: a service reporting per row over a large table turns one call into a flood. Past the cap further reports are dropped — the dispatch is untouched and the final response still arrives. |
| `MAX_REQUEST_BYTES` | `1048576` (1 MiB) | Maximum accepted request body size. |
| `SESSIONS_ENABLED` | `True` | Whether the server mints and requires an `Mcp-Session-Id`. `False` runs the sessionless legacy mode, which is conformant rather than a fallback — but the session id is what addresses a client's SSE channel, so with it gone the `GET` stream has no address and answers `405`. Request/response tool calling is untouched. |
| `SESSION_TTL_SECONDS` | `86400` (24 h) | How long a session may sit **idle** before it expires. The window restarts on every successful read, so a session in continuous use never lapses. |
| `SESSION_MAX_AGE_SECONDS` | `604800` (7 days) | Ceiling on a session's **total** lifetime regardless of activity. Not optional in spirit, though `None` disables it: a session's principal binding is checked once, at `initialize`, so without an absolute cap a sliding idle window keeps a *revoked* principal alive for as long as it keeps talking. A cache-backed store may also evict earlier under memory pressure, which is indistinguishable from expiry on the client side — if sessions vanish early, check the eviction policy before this setting. |
| `SUBSCRIPTION_MAX_SECONDS` | `3600` (1 h) | How long one `subscriptions/listen` stream may stay open before the server closes it gracefully and the client re-subscribes. An authorization control as much as a resource one: a subscription's permissions are checked once, when it opens, so without a cap a principal whose access was revoked keeps receiving change signals for as long as it holds the connection. `None` disables the cap — and both bounds with it. |
| `MAX_CONCURRENT_SUBSCRIPTIONS` | `100` | Ceiling on concurrent subscription streams **per worker**. Each parks an ASGI task for its lifetime, so without a bound an authenticated caller can exhaust the worker pool by opening streams in a loop. Past the cap a subscription is refused with `503` / `-32603` rather than queued. `None` disables. |
| `SSE_STREAM_MAX_SECONDS` | `3600` (1 h) | How long one `GET` session stream may stay open before the server closes it gracefully with a `: stream closed` comment frame. The session stream's counterpart to `SUBSCRIPTION_MAX_SECONDS`, and an authorization control on the same terms: the caller is authenticated once, when the stream opens, so without a cap a principal whose access was revoked keeps receiving that session's pushes for as long as it holds the connection — and it holds it indefinitely, since the keep-alive is what stops any proxy reaping it. An SSE client reconnects by itself; pair with `sse_replay_buffer=` for a gapless reconnect. `None` disables. |
| `MAX_CONCURRENT_SSE_STREAMS` | `100` | Ceiling on concurrent `GET` session streams **per worker**. Each parks an ASGI task for its lifetime and minting sessions is uncapped, so without a bound an authenticated caller opens one stream per session it minted and exhausts the worker pool. Past the cap the `GET` is refused with `503` / `-32603` rather than queued. `None` disables. |
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
| `MAX_PAGE_SIZE` | `100` | Ceiling on the model-supplied `limit` of a `paginate=True` selector tool. Advertised as `maximum` on the generated `inputSchema` *and* clamped at dispatch — the schema tells a well-behaved model what to ask for, the clamp is what stops you trusting it. Clamping is safe here because `totalPages` / `hasNext` keep a clamped page self-describing. The default matches the page a selector tool serves when the model omits `limit` entirely, so an unconfigured deployment never advertises a page larger than the one it defaults to. |
| `DISPATCH_TIMEOUT` | `60.0` | Wall-clock ceiling, in seconds, on one dispatch. **ASGI only** — a sync WSGI view has no in-process way to bound its own dispatch. It does **not** reclaim the worker: a thread parked in a database driver's socket read is not interruptible by asyncio cancellation, so the query runs on. What it buys is a *terminal protocol event* instead of an open request that never resolves. Pair it with a database statement timeout. |
| `REQUIRE_LIST_PAGINATION` | `False` | Registering a LIST selector tool with `paginate=False` raises `ImproperlyConfigured` instead of emitting `UnboundedListWarning`. Such a tool serialises whatever its selector resolves to — the whole table, for a plain `Model.objects.all()` — and unlike a paginated tool there is no honest way to clamp it, because the result carries no metadata saying rows were dropped. `MAX_RESULT_BYTES` is the backstop; `paginate=True` is the fix. |

## Permissions

| Key | Default | What it does |
|---|---|---|
| `REQUIRE_TOOL_PERMISSIONS` | `True` | Registering a **tool, resource or prompt** with no permissions at all (neither `spec.permission_classes` nor `permissions=[…]`) raises `ImproperlyConfigured`. Set it to `False` to downgrade that to an `UnguardedToolWarning` while migrating a large surface. The warning exists because guarding the *viewset* — or relying on `REST_FRAMEWORK`'s default permission classes — has **no effect over MCP**: this package bypasses DRF's view-layer pipeline, so a spec that looks guarded over HTTP ships as an unguarded binding. Interactive views (`register_ui_resource`) are exempt: a view carries no tenant data by construction. See [Authentication](../auth.md). |
| `REQUIRE_TOOL_DESCRIPTIONS` | `False` | Registering a tool with no `description` raises `ImproperlyConfigured` instead of emitting `UndescribedToolWarning`. The description is the only thing a model reads to decide whether and how to call a tool, so an empty one ships a tool that cannot be used correctly — and `tools/list` renders it indistinguishably from a documented one. There is deliberately **no docstring fallback**: a docstring is written for the next developer, not for a model choosing between tools. See [Documenting tools](../concepts.md#documenting-tools). |
| `FILTER_LISTINGS_BY_PERMISSIONS` | `False` | Drop bindings whose `permissions` deny the caller from `tools/list`, `resources/list`, `resources/templates/list` and `prompts/list`. Per-binding `always_listed=True` opts one back in as a discovery aid. |

## OAuth

| Key | Default | What it does |
|---|---|---|
| `DCR_ENABLED` | `False` | Default for `build_oauth_urlpatterns(dcr_enabled=)`. RFC 7591 dynamic client registration; `False` makes `/oauth/register/` refuse every request with 403. An open DCR endpoint lets anyone create an OAuth client against your authorization server — one with no owning user, which is why only the `authorization_code` grant is registerable here. |
| `DCR_INITIAL_ACCESS_TOKEN` | `None` | Default for `build_oauth_urlpatterns(dcr_initial_access_token=)`. The RFC 7591 §3 initial access token clients must present as `Authorization: Bearer …`. `None` means no token check — anyone who can reach the endpoint can register. |
| `SIMPLEJWT_ACCESS_COOKIE` | `"access"` | Default for `SimpleJWTCookieAdapter(cookie_name=)` (`[jwt]` extra) — the cookie it reads access tokens from. Matches `djangorestframework-simplejwt`'s documented `AUTH_COOKIE` default. |

## Observability

| Key | Default | What it does |
|---|---|---|
| `RECORD_SERVICE_EXCEPTIONS` | `False` | Record a `ServiceError` raised from a tool callable on the active OpenTelemetry span before mapping it to a JSON-RPC error. Off by default because services often raise `ServiceError` for routine business-rule denials, which would flood error pipelines. `ServiceValidationError` is *never* recorded — it is client input failure, not a server fault. See [Observability](../observability.md). |
