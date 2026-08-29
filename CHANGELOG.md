# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed

- **`ordering_fields=` is gone from every selector-tool registration surface.**
  Deprecated in [0.30.0] (2026-08-11) and removed here, five minors later. It
  is off `register_selector_tool`, `@selector_tool`, `selector_spec_to_tool`,
  `SelectorToolBinding`, `ToolDefinition.selector` and `SelectorDefaults`, so a
  call still passing it raises `TypeError` at registration rather than being
  quietly ignored.

  Migrate by moving the field list onto the spec's `FilterSet` as an
  `OrderingFilter`:

  ```python
  class InvoiceFilterSet(django_filters.FilterSet):
      ordering = django_filters.OrderingFilter(
          fields=(("created_at", "created"), ("amount_cents", "amount")),
      )
  ```

  ...and dropping `ordering_fields=[...]` from the registration call. The names
  on the right become the choices the model sees — pick ones you are happy to
  expose, because the ORM paths on the left stop being public. A model's own
  `Meta.ordering` still supplies the default order with no argument at all.

  One vocabulary now serves HTTP and every agent transport: the `OrderingFilter`
  is reflected into the tool's `inputSchema` as a labelled `oneOf` and applied
  by the `FilterSet`, which is why the knob's own enum, its dispatch-side
  `order_by(...)`, and the `ImproperlyConfigured` raised when both channels
  declared `ordering` all go with it.

  **Two behaviour notes for anyone on the retired knob.** The advertised values
  change from raw ORM paths (`amount_cents` / `-amount_cents`) to the
  `FilterSet`'s public names, so a client sending the old string gets an
  unrecognised value; and an unrecognised `ordering` is now *rejected* by the
  filter's validation, where the knob silently dropped it and answered with
  rows in an order nobody asked for.

## [0.35.0] — 2026-08-28

### Added

- **`agent_contract=` on every registrar, and `register_specs` reads each
  entry's own.** drf-services carries an `OfflineContract` on the registry
  entry: the `url_kwargs`, `query_params` and `field_audiences` a caller with
  **no HTTP request** has to be told, because the URLconf and query string tell
  an HTTP one for free.

  ```python
  registry.register(
      "list_project_orders",
      orders_spec,
      agent_contract=OfflineContract(url_kwargs=(UrlKwarg("project_pk"),)),
  )

  server.register_specs(registry)  # the tool takes project_pk, declared once
  ```

  This is a **default the mount may override**, not a mandate: a per-tool
  `url_kwargs` / `query_params` override wins, and overriding `agent_contract`
  itself replaces the entry's outright — the only way to mount an entry with
  *fewer* channels than it declares. A server registering a spec directly, with
  no registry in front of it, passes the same object; a chain has no entry, so
  this is its only route.

  **The point is what a second agent transport now sees.** These declarations
  described the same absent request for the same operation, and a project
  running this server *and* an in-process Pydantic-AI toolset declared them
  twice, in two shapes, with nothing comparing them. `field_audiences` was the
  sharp end: it existed here and nowhere in that toolset at all, so one spec
  projected a different field set depending on which transport served it — an
  audience is not a transport, and a field hidden from one agent caller and
  visible to another is something you find in a transcript rather than a test.

### Changed

- **One name for the output serializer, across all four binding kinds.**
  `ToolBinding`, `SelectorToolBinding` and `ChainToolBinding` exposed
  `agent_output_serializer`; it is now `output_serializer`, which
  `ChainToolBinding` and `ResourceBinding` already called it.

  Two reasons, and the second is the better one. It was a leak by the rule
  drf-services 0.48.0 settled — "agent" is earned where a name marks an audience
  the serializer author declares, and a leak where it marks only which callers
  happen to use it — and nothing about *which serializer produces the output* is
  agent-specific. But it was also **one idea spelled twice**: `ChainToolBinding`
  already carried an `output_serializer`, and its `agent_output_serializer` was
  `return self.output_serializer`. So this deletes a member rather than renaming
  one, and the four binding kinds now answer the same question by the same name.

  These bindings share no base class, so a test pins the agreement rather than a
  signature.



- **Floor raised to `djangorestframework-services>=0.48`, and it is a hard
  floor.** 0.48 renamed ten public symbols with **no deprecation aliases**, so
  the names this package imports at module level do not exist below it. Nothing
  in this package's own API changed shape as a result — only which upstream
  names it reaches for:

  | Was | Now |
  | --- | --- |
  | `AgentField` | `FieldMarking` |
  | `AGENT` | `MARKING` |
  | `AgentProjection` | `AudienceProjection` |
  | `AgentContract` | `OfflineContract` |
  | `build_agent_projection` | `build_audience_projection` |
  | `render_for_agent` | `render_for_audience` |

  The `agent_contract=` argument on every registrar keeps its name — it is
  still what the registry entry's field is called — and only its type moved.
  Prose that says "agent" because an agent is what is being described is left
  alone; the rule upstream applied is that "agent" is earned where a name marks
  an audience the serializer author declares, and a leak where it marks only
  which callers happen to use it. This package **is** an agent transport, so
  `field_audiences`, `append_agent_conventions` and the `agent_contract=`
  argument all keep theirs.

- **`ToolBinding.agent_projection` is now `audience_projection`**, on all three
  tool bindings, following the type it returns. A breaking rename of a public
  attribute, with no alias for the same reason upstream declined one: every
  known reader is in this family and moves in the same pass.

- **A `FilterSet`'s choice labels now reach the `inputSchema`.** With
  drf-services 0.47+, a `ChoiceFilter` whose labels differ from its values is
  published as `{"oneOf": [{"const": …, "title": …}, …]}` instead of a bare
  `enum`, and a filter gains `title` from its `label` and `description` from
  its `help_text` — or a derived ``"Matches `views` with the `gte` lookup."``
  where the argument's own name gives neither the column nor the comparison
  away.

  **`OrderingFilter` subclasses `ChoiceFilter`, so a FilterSet-declared
  `ordering` changes shape**, and that is the case most likely to reach an
  existing client: it now says that `-amount` means "Amount (descending)"
  rather than leaving a model to infer direction from a leading minus sign. A
  client reading `properties["ordering"]["enum"]` off `tools/list` needs
  updating; the accepted value set is unchanged, since `title` annotates and
  constrains nothing.

  The deprecated `ordering_fields=` registration knob is unaffected — this
  transport builds that schema itself out of bare ORM paths and has no labels
  to attach.

### Removed

- **The `field_audiences=` registration argument, superseded before it shipped.**
  It was wired onto the five registrars in an unreleased change, then replaced
  by `agent_contract=` — one carrier, of the type the registry entry already
  holds, rather than a sixth place to declare an audience override. It reached
  no release, so nothing to deprecate.

  The bindings keep their `field_audiences` field, which is what the contract
  resolves to.

- **`resolve_agent_projection`, moved upstream.** The merge of a mount's
  overrides over the serializer's own markings — and the two-labels clash an
  override can introduce — now lives in drf-services'
  `build_audience_projection(overrides=…, name=…)`, so both agent transports
  layer the shared declaration by the same rule instead of each carrying a
  copy. It was private to the three tool bindings; no import path changes.

- **`_slice_for_pagination`, moved upstream.** A paginated selector tool's page
  is now shaped by drf-services' `paginate_output`, and its envelope by
  `OutputPage.envelope` — the clamps (`limit` down to the ceiling and up to 1,
  `page` up to 1 and down to the last page that exists), the count taken before
  the slice, and the `totalPages` / `hasNext` arithmetic, all in one place.

  The two implementations were compared over 13 argument shapes against 4 row
  collections before the local one was deleted: 52 comparisons, no differences.
  Behaviour is unchanged apart from the wording of the `TypeError` a paginated
  selector returning a generator raises, which is now drf-services' and says
  "serve this selector unpaginated" where this package said "set
  `paginate=False`".

  **`page` and `limit` are still parsed here**, and deliberately so. Turning an
  untyped JSON argument into an integer is where transports legitimately
  differ — a public MCP endpoint clamps a malformed value and answers, while an
  in-process toolset can hand the model its mistake back and ask again — so
  that stays a policy this transport owns, and only already-parsed ints cross
  the boundary. It is why the default page size now comes from
  `DEFAULT_PAGE_SIZE` rather than a literal `100` in two packages.

### Security

- **Audience enforcement is available out of the box and the docs said it was
  impossible.** django-oauth-toolkit **3.4.0** (2026-07-23) added RFC 8707
  resource indicators: stock `AccessToken` carries a `resource` field and an
  `allows_audience()` check. Four places in this package still told adopters
  otherwise — `docs/auth.md` twice, `docs/reference/settings.md`, and the
  `DjangoOAuthToolkitBackend` docstring — all asserting DOT "has no such field
  and implements no RFC 8707 resource indicators".

  **The consequence was security guidance, not tidiness.** The MCP `2026-07-28`
  spec makes audience validation a **MUST** for a resource server, and a reader
  concluded enforcement was impossible with stock DOT — so they left
  `ENFORCE_AUDIENCE` off, failing that MUST, or built an `audience_getter`
  workaround they no longer needed.

  The runtime check was already correct and capability-based, and the
  `ImproperlyConfigured` message already named 3.4.0. **That is the dangerous
  shape**: someone corrected the error message when DOT 3.4 landed and did not
  propagate it, and a partially-corrected claim is worse than a uniformly stale
  one, because the code being right makes the prose look trustworthy.

- **`UnenforcedAudienceWarning` — a deployment that could enforce and does not
  is now told so.** The default stays `False`, because the `[oauth]` extra
  floors DOT at `>=2.3` and a default of `True` would reject every request for
  anyone below 3.4.0. But that reason expires **per deployment**, when a project
  upgrades DOT, and it expired silently.

  The warning fires only where enforcement would actually work — the configured
  token model carries the field and a resource URL is set — so it is a fact
  about *that* deployment rather than advice about the package, and it cannot
  fire on the older DOT the default exists to protect. It warns rather than
  raising, and rather than flipping the default: a single-resource server a
  project fully controls is a legitimate place to skip enforcement.

### Documentation

- **The official `mcp` SDK publishes protected-resource metadata for you and
  never validates the audience.** `resource_url_from_server_url()` and
  `check_resource_allowed()` are called only from client paths;
  `BearerAuthBackend.authenticate()` checks the bearer prefix, the verifier's
  verdict and `expires_at`, and nothing else. So a `TokenVerifier` that merely
  checks a signature is non-compliant with the audience MUST and cross-resource
  token replay works. Documented in `docs/auth.md` because it is the easiest
  thing to get wrong in this area and is not specific to this package.

- **The MCP conformance claim named only the older revision, at four sites.**
  `README.md` and `docs/index.md` each said "conforming to MCP 2025-11-25" in
  the intro *and* described the transport rules as that revision's, while the
  server has been dual-era since 0.34.0 — `2026-07-28` and later declare version,
  identity and capabilities per request and hold no session; `2025-11-25` and
  earlier negotiate through `initialize`. Both are served concurrently on one
  endpoint.

- **`docs/reference/output.md` documented no output.** Eight lines: a heading,
  one sentence and four mkdocstrings directives, with the string `outputSchema`
  appearing nowhere. It now states the asymmetric rule between
  `structuredContent` and `outputSchema`, both toggles, and links the canonical
  passage in `concepts.md`.

- **`additionalProperties` has three drivers, and `concepts.md` named two.** The
  third arrived in 0.34.0: a **service** tool whose declared key set is not
  enumerable — a nested selector taking bare `**kwargs`, or carrying a
  `filter_set` — advertises an open schema even under `REJECT` with a
  serializer, because an open set is answered by accepting and silently dropping
  undeclared keys. Where nothing is enforced, nothing closed may be advertised.

- **The `field_audiences` clash raises on first use, not at registration.** The
  recipe implied startup. The projection is resolved lazily, deliberately —
  resolving a serializer at registration would run before the app registry is
  necessarily ready — so the recipe now says when, and suggests a smoke test
  that lists the tools once.

## [0.34.0] — 2026-08-26

### Upgrade notes

**The page ceiling now bounds a selector tool's rows, not only its `limit`.**
`MAX_PAGE_SIZE` (default 100, per-binding `max_page_size=`) has always been the
most rows one page of a `paginate=True` tool may carry. It now means the most
rows *any* selector-tool result carries, which brings two previously unbounded
calls inside it:

- A `paginate=False` LIST tool whose selector resolves to more rows than the
  ceiling now returns an `isError` result instead of the whole table. It is
  refused rather than truncated, for the reason the registration-time
  `UnboundedListWarning` has always given: an unpaginated payload carries no
  metadata that could say rows were dropped, so a clamped one reads as
  complete to the model reasoning from it.
- A `page` argument past the last page now clamps to the last page instead of
  compiling into an arbitrarily large SQL `OFFSET`.

Both are configured through the knob that already existed: pass
`max_page_size=None` at registration to serve one tool unbounded, or set
`REST_FRAMEWORK_MCP['MAX_PAGE_SIZE'] = None` for the server. A deployment
relying on an unpaginated tool that returns more than 100 rows must do one of
those, or register it with `paginate=True`.

**`REST_FRAMEWORK_MCP['PAGE_SIZE']` below 1 is now refused.** A catalog listing
raises `ImproperlyConfigured` naming the setting instead of serving empty pages
behind a cursor that never terminates.


- **Dynamic client registration.** If you relied on `/oauth/register/` to issue
  `client_credentials`, `password` or `implicit` clients, those registrations
  now return `400 invalid_client_metadata`. Create such applications through
  Django admin or a management command, where an owner and a review step exist,
  and keep DCR for the interactive `authorization_code` clients it is for. Note
  that `AuthorizationServerMetadata.grant_types_supported` never advertised the
  other three.

- **DCR redirect URIs.** The accepted schemes now come from
  `OAUTH2_PROVIDER["ALLOWED_REDIRECT_URI_SCHEMES"]` (django-oauth-toolkit's
  default is `["http", "https"]`). Native clients registering a private-use
  scheme need that setting widened — the same setting the authorization request
  already checked. Conversely, `ftp://` and `ftps://` redirect URIs, which
  DRF's `URLField` used to admit, are now refused unless the setting names them.

- **`resource_link` tools.** A tool with `content_kind=RESOURCE_LINK` whose
  payload can carry a `file:`, `data:` or other non-fetchable URI now returns an
  `isError` result for that call instead of emitting the block. If your resource
  URIs use a custom scheme (`reports://`, `docs://`), they are unaffected —
  only the script-bearing and local-content schemes are refused.

- **Resources registered from a rich `SelectorSpec`.** A registration that sets
  any of the ten fields listed above now raises at startup rather than silently
  dropping them. Register the spec as a selector tool, or move the behaviour
  into the selector callable.

- **`structuredContent` on a null payload.** A tool whose answer is `null` now
  emits `"structuredContent": null` instead of omitting the key. Clients that
  treated the key's absence as "no value" see no change in meaning; clients that
  treated it as "this tool has no structured output" were being misled.

### Added

- **The `GET` session stream is bounded, on both axes.** It had neither a
  concurrency cap nor a lifetime: `stream_events` was a bare `while True` whose
  only exit was the client leaving, keep-aliving itself past any proxy timeout,
  while minting sessions stayed uncapped — so an authenticated caller could
  loop `initialize` and open one parked ASGI task per session. Two new settings,
  mirroring what `subscriptions/listen` already had:

  - `MAX_CONCURRENT_SSE_STREAMS` (default `100`) — past it a `GET` is refused
    with `503` / `-32603` rather than queued.
  - `SSE_STREAM_MAX_SECONDS` (default `3600`) — the stream closes gracefully
    with a `: stream closed` comment frame, and the client reconnects. It also
    bounds how long a revoked principal keeps receiving a session's pushes,
    authentication being checked once at open.

  Both take `None` to disable. **Upgrade note:** these are new limits where
  there were none. A deployment that deliberately holds more than 100 concurrent
  streams per worker, or streams open for longer than an hour, should set them
  explicitly. A client without an `sse_replay_buffer=` may miss notifications
  published during a reconnect, which was already true of any disconnect.

  Custom `SSEBroker` implementations must now expose an `active_streams`
  property (the count of local subscribers) — both bundled brokers do.

- **The SSE brokers bound their per-session queue.** `InMemorySSEBroker` and
  `RedisSSEBroker` take `max_queued_events=` (default `1024`) and drop the
  oldest payload past it, returning `False` from `publish` to report the drop
  through the channel that already meant "nobody got this". A client that opened
  the stream and stopped reading it previously accumulated every published
  payload in memory with no drop policy and no backpressure. **Upgrade note:** a
  notification can now be dropped for a stalled reader where it would previously
  have been retained; delivery was always documented as best-effort.

- **The SSE replay buffers stop accumulating dead sessions.** `forget()` runs
  only on an explicit `DELETE`, while sessions ordinarily end by expiring or by
  a client simply dropping the connection, so every session that ever recorded
  an event kept its ring and its counter for the life of the process (or of the
  Redis instance). `InMemorySSEReplayBuffer` takes `max_sessions=` (default
  `1024`) and drops the least recently written; `RedisSSEReplayBuffer` takes
  `ttl_seconds=` (default one day, matching the session store's idle window),
  refreshed on every write. **Upgrade note:** a resume for a session evicted or
  expired this way replays nothing rather than replaying stale events — the same
  silent gap a client already accepts when its ring overflows.

- **`RedisSubscriptionBroker` and `RedisSSEReplayBuffer` take a `namespace=`.**
  Both used a fixed key prefix while the cache-backed stores fold the server's
  `name` into theirs. Subscription topics are built from caller-supplied values
  — a notification kind, a resource URI — so two servers in one project that
  register the same resource URI derive the same topic, and a subscriber
  authorized on one could receive the other's change signals, routing around the
  `resources/read` check `grant_subscription` gates subscriptions on. Pass
  `namespace=` (the server's name is the obvious value) whenever one Redis
  serves more than one server. The default prefix is unchanged, so a
  single-server deployment sees no key churn.

### Fixed

- **A tool's `invalidates=` announcements now reach subscribers on WSGI when the
  server is mounted through `.urls`.** The sync transport had nowhere to publish
  them, so a tool that declared `invalidates=` committed its write and told
  nobody — indistinguishable, from a subscriber's side, from the resource never
  having changed. `async_urls` always passed the broker through, so the gap was
  visible on ASGI and invisible on WSGI.

- **`prompts/get` names a malformed `arguments` field instead of treating it as
  empty.** `[]`, `""`, `0` and `False` were folded into "no arguments" by the
  line immediately above the one that exists to reject a non-object, so a prompt
  whose arguments are all optional rendered a call the client never made in that
  shape, and every other prompt answered with a confusing missing-argument error
  rather than naming the real fault. Matches the same correction on `tools/call`.

- **A selector tool's object-level permissions never ran.** A `SelectorSpec`
  carrying `permission_classes` had only its class-level `has_permission`
  enforced over MCP: the spec's `has_object_permission` needs the resolved row,
  which only dispatch sees, and this path passed no `on_target_resolved` guard —
  so a spec whose ownership test lives there was enforced behind a DRF view and
  on the service-tool path, and not here. A `kind=RETRIEVE` tool would render
  another principal's row to any caller the class-level check let through.
  Both dispatch paths now pass `enforce_permissions` as the guard and map its
  denial to a JSON-RPC `-32006`, as the service-tool path does. A LIST target is
  a queryset rather than a model, so it runs the class-level check only, which
  is what object permissions mean per row.

- **An unpaginated LIST tool fetched and serialised the entire queryset.**
  `MAX_RESULT_BYTES` was the only backstop and it measures a payload that has
  already been fetched and rendered in full, so the whole table was in memory
  before the ceiling could fire. The rows are now bounded in SQL, one past the
  ceiling, before anything is rendered. See the upgrade notes.

- **A doubled sign in `ordering` escaped as an unhandled 500.** The allowlist
  normalised the value with `lstrip("-")`, which strips *characters* rather than
  one sign, so `--created_at` matched an allowed `created_at` and reached
  `order_by()` verbatim — where Django's ordering pattern rejects it with a
  `ValueError` that no handler on the tool-call path catches. The client got a
  traceback instead of a JSON-RPC envelope for a mistyped argument. Exactly one
  leading `-` is stripped now, so a doubled sign fails the allowlist and is
  ignored like any other unrecognised ordering.

- **A paginated tool's `page` argument had no upper clamp.** `(page - 1) *
  limit` was whatever the caller asked for, so a large `page` compiled to an
  `OFFSET` the backend either scanned towards or rejected with a `DatabaseError`
  that is neither exception this path catches — another unhandled 500, and a
  `count()` on every such call regardless. See the upgrade notes.

- **A `PAGE_SIZE` below 1 produced a cursor that never terminated.** Every page
  came back empty, so `nextCursor` re-encoded the offset it was handed for as
  long as the registry was non-empty, and a conformant client following it saw a
  catalog that looked permanently empty and never finished paging. See the
  upgrade notes.

- **A `SelectorSpec.kwargs` provider's `UNSET` decline is honoured on
  `resources/read`.** Returning `UNSET` means "I am not setting this key", and
  the sentinel was being written into the pool instead — inverting a decline
  into an override, and handing the ORM a sentinel for a well-formed request.

- **`MAX_RESULT_BYTES` applies to `prompts/get`.** It was enforced on
  `tools/call` and `resources/read` and silently skipped for the one method
  whose body is whatever a consumer's `render` callable returned, so an
  operator who set the ceiling believed every result surface was bounded.


- **A tool call answered as a task now charges its rate limits.** It charged
  them exactly zero times: `maybe_create_task` answers *before* dispatch, which
  is where the inline charge lives, and the worker replays the call under
  `enforce_rate_limits=False`. Each side's docstring said the other one paid.
  A client that declared the tasks extension therefore opted itself out of every
  quota a `task_policy=OPTIONAL`/`REQUIRED` tool configured — by declaring a
  capability — and the enqueue loop was unbounded. The charge now happens in
  `maybe_create_task`, after the permission check and before anything durable
  exists, so an exhausted quota is refused with `-32005` and leaves no record
  and no queued job behind. The worker still charges nothing, which is what
  keeps it exactly one charge per client call.

  **Upgrade note.** This is a behaviour change for any deployment already
  running task-shaped tools with `rate_limits=`: those calls were free and are
  now billed. If a quota was sized against the inline path only, it will now
  also see the task-shaped traffic.

- **`tasks/*` no longer runs on the event loop under ASGI.** `adispatch` fell
  through to the sync handler table inline, so `tasks/get` — the only way to
  collect a task result — read the task store from the loop. With the default
  `DjangoCacheTaskStore` over a `DatabaseCache` that raises
  `SynchronousOnlyOperation`, leaving a client able to create tasks it could
  never collect. The fall-through now goes through the thread-sensitive
  executor, which also covers the list handlers, where a consumer's
  `DjangoPermRequired` runs a query.

- **The WSGI transport can now publish `invalidates=` announcements.** The sync
  viewset built its `MCPCallContext` without a subscription broker, so
  `publish_invalidations` returned without touching one and every subscriber was
  told nothing — indistinguishable from the resource never having changed —
  while `MCPServer`'s own sync contexts (`call_tool`, the task worker) have
  always carried the broker. `StreamableHttpViewSet` now takes
  `subscription_broker=` and threads it through both of its context
  constructions, so a tool mutating over WSGI reaches subscribers whose streams
  are parked on an ASGI process: the split a cross-process broker exists for.
  Publishing only — serving `subscriptions/listen` still needs the async
  transport, which is what can hold a stream open.

- **A DRF permission reading `request.auth` no longer resets the user to
  `AnonymousUser`.** `DRFPermissionAdapter` set `.user` but left `_auth`
  unresolved, so the first read of `request.auth` ran DRF's (empty)
  authenticator chain, which ends in `_not_authenticated()` and overwrites both
  the wrapper's user and the underlying `HttpRequest`'s. Permissions as ordinary
  as django-oauth-toolkit's `TokenHasScope` therefore denied properly scoped
  callers over MCP with no diagnostic. The adapter now assigns
  `request.auth = token.raw` alongside the user.

- **A task worker honours a deactivated account.** `build_worker_token` re-read
  the user so a revocation would be caught at run time, but nothing downstream
  consults `is_active` — `IsAuthenticated` is true of an inactive user — so the
  re-read honoured deactivation in appearance only. An inactive user now
  degrades to `AnonymousUser`, as a deleted one already did. The docstring also
  now states plainly what is *not* re-derived: `scopes` and `audience` are
  replayed as frozen at creation, because the backend handle that could
  re-validate them is deliberately not persisted; `TASK_TTL_MS` bounds that
  window.

- **A present-but-unsupported `MCP-Protocol-Version` is rejected on sessionless
  requests too.** `negotiate_protocol_version` documented that it always
  rejects one and then silently downgraded it on `initialize` and
  `server/discover`. A header naming a version this server does not support in
  either era is now a `400` on every path. A header naming a *modern* version
  still takes the legacy fallback: the server does support it, just not through
  this handshake, and `initialize`'s own era check is where that is explained.

- **A malformed `taskId` or `Mcp-Session-Id` is answered as a miss.** Both go
  straight into a Django cache key, and the memcached backends reject keys
  holding a space or a control character, or over 250 bytes — raising out of a
  handler with no arm for it, so a client got an unhandled `500` where the
  protocol says "unknown task" / "unknown session". Both cache-backed stores now
  check the id against the shape they mint before touching the cache. Nothing is
  leaked: an id that cannot be one they issued names nothing that exists.

- **`build_mcp_config(task_ttl_ms=None)` now means "never expire".** Both task
  scalars used the `x if x is not None else setting` shape, which reads an
  explicit `None` as "not supplied" — so asking for tasks that never expire
  silently got the setting's 24 hours instead, and the only sign was records
  vanishing a day in. They now use the `UNSET` sentinel the other nullable
  bounds use. `REST_FRAMEWORK_MCP['TASK_TTL_MS'] = None` was always honoured and
  still is.

  **Upgrade note.** `build_mcp_config(task_ttl_ms=None)` and
  `build_mcp_config(task_poll_interval_ms=None)` previously fell back to the
  setting. If you were passing `None` to mean "use the setting", drop the
  argument.


- **`tools/list` no longer advertises `additionalProperties: false` on a
  guarantee the runtime does not provide.** For a service tool the
  unknown-argument check runs against the key set the spec declares, and that
  set is not always enumerable — a nested selector taking a bare `**kwargs`, or
  carrying a `filter_set`, leaves it open, and an open set is answered by
  accepting and silently dropping every undeclared key. Those tools now
  advertise an open schema, so a client is no longer told a typo'd field will be
  refused while the server takes it and throws it away.

- **A client-supplied `outputFormat` is validated before the tool runs.** An
  unrecognised value reached `OutputFormat.coerce` at the *end* of dispatch,
  past the last `except`, so it raised out of the handler as a bare HTTP 500
  with no JSON-RPC envelope — after the mutation had committed, leaving the
  caller unable to tell whether a retry would apply the write twice. It is now
  a `-32602` returned ahead of task creation and dispatch, on both the sync and
  the async path.

- **A malformed `arguments` field is named as the fault.** `params.get(
  "arguments") or {}` collapsed `[]`, `""`, `0` and `false` into an empty dict
  before the guard on the next line could see them, so a tool whose inputs are
  all optional ran a call the client never intended in that shape, and every
  other tool answered with a confusing missing-field error. Only a missing key
  or an explicit `null` now means "no arguments". Both `tools/call` paths.

- **An explicit `null` no longer satisfies a `required=True` `UrlKwarg`.** A URL
  kwarg stands in for a route capture, which can never be null over HTTP;
  off-HTTP `{"pk": null}` was treated as supplied, so the required check never
  fired and the `None` was seeded into `view.kwargs`, where `.filter(pk=None)`
  becomes SQL `IS NULL` — an unscoped read that answers successfully with the
  wrong rows. A null now falls through to the declared default and then to the
  required check, exactly as an omitted key does.

- **`structuredContent` distinguishes a null answer from no structured channel.**
  A tool called with `include_structured_content=True` whose payload was
  genuinely `None` produced the same wire shape as one that had opted out, so a
  client branching on the key's presence was told the server offers no
  structured output. A null payload is now emitted as `"structuredContent":
  null`; the key is omitted only when structured content was not requested.

- **The `# format: toon` marker is stamped only when TOON produced the text.**
  Without the optional `[toon]` extra the encoder warns and falls back to JSON,
  and that warning goes to the server's log, not onto the wire — so a client
  selecting its parser from the marker line was handed JSON labelled as TOON.
  The fallback now ships as plain, unmarked JSON.

- **`ResourceRegistry.resolve` prefers a concrete URI over a template.**
  Resolution walked registration order, and a template's `{var}` matches any
  single segment — so `reports://{report_id}` also matched
  `reports://all-tenants-summary`, and which permission stack guarded a URI
  depended on the order the two were registered in. Concrete URIs are tried
  first, then templates.

- **The DCR initial access token is compared in constant time.** `!=` returns at
  the first differing byte, which over enough requests leaks a bearer credential
  one prefix at a time; recovering it turns a gated registration endpoint into
  an open one. Now `secrets.compare_digest`, on bytes so a non-ASCII header is a
  401 rather than a crash.

- **`scripts/benchmark.py` runs again.** It imported `handlers.context` and
  `auth.token_info`, both moved under `types/` sub-packages, and mounted
  `server.urls` through `include()`, which no longer accepts the namespaced
  triple. A new smoke test checks every script's package imports still resolve,
  since nothing else in the gates covers `scripts/`.

### Changed

- **Dynamic client registration accepts only the `authorization_code` grant.**
  A dynamically registered client has no owning user and no human in the loop,
  so the grant it holds must be one that cannot mint a token without one.
  `client_credentials` can: the resulting token carries no user at all, and the
  scope permission tests only the token's scopes, so it satisfies every
  scope-gated tool — an open registration endpoint became an unauthenticated
  path onto the whole tool surface. `password` and `implicit` are removed from
  OAuth 2.1 outright. The narrowing applies to DOT's `authorization_grant_type`
  spelling too, and matches what `grant_types_supported` already advertised.

- **DCR `redirect_uris` accepts any absolute URI on a scheme the authorization
  server will honour.** DRF's `URLField` allowlists the http family, so the
  endpoint refused exactly the private-use schemes RFC 8252 §7.1 defines for the
  native clients its own `application_type` exists to describe — while admitting
  `ftp`, which no OAuth client redirects to. The accepted set is now
  django-oauth-toolkit's own `ALLOWED_REDIRECT_URI_SCHEMES`, so a registration
  is refused only where the authorization request would have been refused
  anyway.

- **`resource_link` content blocks refuse script-bearing URI schemes.** The
  payload field holding a URI is frequently a row an end user wrote, and the
  block was emitted verbatim to a host that may render it as a clickable anchor
  in its own origin or fetch it to build a preview. `javascript:`, `data:`,
  `vbscript:`, `blob:`, `file:` and `about:` URIs, and anything that is not an
  absolute URI, now come back as the same explanatory tool-level error the other
  payload mismatches use.

- **Registering a resource from a `SelectorSpec` that sets a field the read path
  cannot apply is refused.** `resources/read` dispatches the selector callable
  directly, so `preconditions`, `select_related`, `prefetch_related`,
  `annotations`, `extend_queryset`, `filter_set`, `output_serializer_context`,
  `allow_none`, `progress_reporter` and `metadata` were dropped with no warning
  — and a `preconditions` gate that holds on every other transport while simply
  not running here is indistinguishable from success. Registration now names the
  fields and points at the selector *tool* surface, which honours them.

- **`UrlKwarg` / `QueryParam` defaults tolerate either spelling of "no
  default".** The declarations live in djangorestframework-services, where the
  sentinel is moving from `None` to that package's `UNSET`. Both are read as
  "no default", so this package is correct against either release and needs no
  version floor raise.

### Security

- **Object-level permissions now run on every dispatch path.** A
  `SelectorSpec` / `ServiceSpec` whose ownership test lives in
  `has_object_permission` was enforced for its class-level half only over MCP,
  so a spec that held on every other transport handed one tenant's row to
  another here. `resources/read` now guards the value its selector resolved,
  and a chain step guards the target it resolved, both through
  djangorestframework-services' `enforce_permissions` — the guard the tool
  paths already pass to `dispatch_spec`. A denial answers `-32006` like any
  other, and inside an atomic chain it rolls the transaction back. A `LIST` /
  collection result still gets the class-level check only: object permissions
  are a per-row concept and a set is authorized per-set.

- **A chain step's `preconditions` run.** They fired only from inside
  `dispatch_spec`, which a chain does not use, so a state rule declared once on
  a spec silently did not hold inside a chain tool while holding everywhere
  else. Ordering matches the rest of the package: permissions on the resolved
  target, then preconditions, then the service or selector.

- **A client argument can no longer stand in for the authenticated identity.**
  `prompts/get` spread the caller's arguments *over* the `request` / `user`
  seeds, so `{"topic": "x", "user": 7}` against the documented
  `def render(user, topic)` shape rendered the prompt scoped to a principal the
  caller named. The same held for a URI-template variable on `resources/read`
  and for a chain step whose `inputs` callable forwards `ctx.args`. On all
  three the transport's seeds are now applied last. `completion/complete` used
  to re-seed four of the seven reserved names after the client's siblings; it
  now drops every reserved name from the spread instead of tracking a
  hand-picked subset.

- **Resources and prompts are held to `REQUIRE_TOOL_PERMISSIONS`.** The
  unguarded-binding check covered tool registrations only, so the same selector
  registered as a resource started clean and answered any principal the
  transport authenticated, while registered as a tool it raised at startup.
  `register_resource` and `register_prompt` now report through the same check
  and the same setting. Interactive views (`register_ui_resource`) stay exempt,
  deliberately: a view is a template rendered with no context, a literal
  document, or a zero-argument callable, so it carries no tenant data.

  **Upgrade note.** A project registering resources or prompts without
  permissions will now fail at startup. Declare `permission_classes` on the
  spec (or pass `permissions=[...]`), or set
  `REST_FRAMEWORK_MCP["REQUIRE_TOOL_PERMISSIONS"] = False` to downgrade it to
  an `UnguardedToolWarning` while migrating.

- **A URI-template variable is validated at registration.** It is a
  caller-controlled name routed into the selector's kwarg pool, and unlike a
  tool's `url_kwargs` / `query_params` it went through no check at all — so
  `notes://{user}/{pk}` compiled to a regex group named `user`. Template
  variables now go through the same shared `validate_channel_names` as every
  other name channel, which also catches a variable declared twice. The
  pagination names are deliberately not reserved here: a resource has no
  post-fetch pipeline, so `docs://{page}` stays a legitimate locator.


## [0.33.0] — 2026-08-25

### Added

- **Tool results are projected for the audience that reads them.** A serializer
  written for a REST API is handed to the model verbatim when the same spec is
  exposed as a tool, so records get named by primary key, a status reads as
  `PENDING_REVIEW` rather than "Awaiting review", and an ETag gets narrated as
  content. Mark the fields once, on the serializer, with `AgentField` from
  djangorestframework-services:

  ```python
  extra_kwargs = {
      "id": {"style": {AGENT: AgentField.handle("Invoice handle.")}},
      "etag": {"style": {AGENT: AgentField.hidden()}},
      "number": {"style": {AGENT: AgentField.label()}},
  }
  ```

  Every dispatch path now renders through `render_for_agent`, so hidden fields
  leave the payload and a choice field's display value replaces its constant —
  except on a handle, which is another tool's input and is never re-spelled.
  The same markings drive the advertised `outputSchema`, generated from the one
  declaration so it cannot advertise a field the payload no longer carries, and
  a handle's `description` reaches the schema entry a model reads beside it.

  Removed rather than relocated: a tool result is emitted as `structuredContent`
  **and** rendered into a text content block, so a subtree of "internal" fields
  would cost its keys twice over and hide nothing.

  **Both sentences a model reads are owned here**, not upstream:
  `HANDLE_DESCRIPTION` for an unlabelled handle's `outputSchema` entry, and the
  conventions line appended to the tool description. drf-services holds the
  markings and no wording — it says what a field *is*, and only a transport
  knows what kind of reader is on the other end.

  Tool descriptions gain one generated line naming the label field and the handle
  convention — only for tools that actually have a handle, since a description is
  read on every listing.

  A chain projects each step through its own spec's serializer, and a paginated
  list projects the items, never the `page` / `totalPages` / `hasNext` envelope
  this server owns.

- **`field_audiences` on all three tool bindings** — per-tool overrides for the
  case one tool needs what a sibling hides. The serializer stays authoritative.
  Overrides leaving two fields marked as the label raise `ImproperlyConfigured`
  naming the tool.

### Changed

- **`outputSchema` now describes read-only fields.** They were being dropped:
  drf-services' output path reused its input walker, which skips `read_only` by
  design. Tools were advertising a shape their own results did not match — most
  visibly by omitting the primary key. Fixed upstream in drf-services 0.43.0.
  `required` on an output schema now lists every rendered field.

- **Floor raised to `djangorestframework-services>=0.43`** for the audience API
  and the schema fix above.

### Fixed

- **The floor-resolution CI gate could resolve against a stale package index.**
  Its purpose is to answer "what would a consumer installing from scratch get" —
  the comment above it cites a real incident where only a consumer resolving from
  scratch caught a bad floor. But both of its resolutions read the runner's
  shared uv cache, so the answer came from whatever package list that cache held
  rather than from the index. A sibling's gate failed a floor raise as
  unsatisfiable while the index had been serving the release for some time; two
  re-runs reused the same cache and failed identically, and a cacheless resolve
  of the same requirement succeeded at once. Both resolutions now use
  `--refresh`, so the gate measures what it claims to. A stale listing was
  dangerous in both directions: it can invent a broken floor, or hide a real one.

## [0.32.1] — 2026-08-24

### Fixed

- **The reST literal-block marker no longer reaches the page.** Sphinx reads a
  trailing `::` as "an indented literal block follows" and prints one colon;
  Markdown has no such rule, so the second colon rendered verbatim. The indented
  block was already coming out as a code block either way, so this drops the
  stray character and nothing else. 20 occurrences, the last of the Sphinx
  markup this package carried.

- **Docstring cross-references now render as links instead of raw markup.** The
  docstrings carried Sphinx roles — ``:class:`~rest_framework_mcp.MCPServer` `` —
  but the docs build is mkdocstrings, which renders docstring bodies as Markdown
  and has no such syntax, so all 389 reached the published page verbatim,
  `:class:` prefix and Sphinx's abbreviating `~` included. Five of them were in
  the narrative recipe pages rather than docstrings. They are now mkdocstrings
  autorefs links; references to symbols the reference does not render, and to
  third-party symbols, became plain code spans.

  The reference still shows raw roles inside the `QueryParam`, `UrlKwarg`,
  `UnknownArguments` and `ArgumentBinding` sections; those come from
  drf-services' own docstrings and clear when its fix is released.

- **Docstring code samples are syntax-highlighted again.** Six `.. code-block::`
  directives had the same problem as the roles: mkdocstrings renders Markdown,
  so the directive line itself reached the page as a literal paragraph
  (`<p>.. code-block:: text</p>`) above an unhighlighted block. They are now
  fenced blocks carrying their language.

- **Neither worked example could be started.** Both mounted the server by
  passing `server.urls` to `include()`, and that property returns the namespaced
  `(patterns, app_name, namespace)` triple `path()` takes directly — the
  `admin.site.urls` idiom — so Django refused the URL conf outright with
  "Passing a 3-tuple to include() is not supported". Both settings modules also
  named a `WSGI_APPLICATION` module that was never written, which is what
  `manage.py runserver` loads. The invoicing example gains the `wsgi.py` its
  README's `runserver` line always implied; the job-status example is ASGI by
  design and drops the setting instead.

### Documentation

- **A recipe for driving this server from a Pydantic-AI agent**, and a scheduled
  job that keeps it honest. `MCPToolset("https://…/mcp/")` is a one-liner against
  the Streamable-HTTP endpoint, and the page names the choice it implies: an
  agent running inside the same Django process wants
  `djangorestframework-pydantic-ai`'s in-process `SpecToolset` instead, over the
  same specs and the same reflection, with no socket in the path.

  The claim worth writing down is about elicitation. This server asks its
  question the way the current spec revision does — the question rides in a
  `tools/call` result and the client retries the original call — and that is a
  different mechanism from the server-initiated request the older revisions
  used. **A current client implements it**: with the client stack that resolves
  to the 2.x MCP SDK, a service raising `AdditionalInputRequired` reaches the
  agent's `elicitation_handler`, and the answer completes the original call with
  the retry, the accumulated state and the second round trip staying inside the
  toolset. On the client stack that resolves to the 1.x SDK the call degrades to
  an error result naming the missing input, as documented, and the handler is
  never invoked. Both pairings are asserted by `scripts/interop_pydantic_ai.py`
  in the weekly `upstream drift` workflow, against a real socket rather than a
  fixture: a foreign client reading the wire is the only thing that can catch us
  reading our own spec generously.

  Consumers on that client should expect a `UserWarning` saying the handler
  "will never be called". It is wrong here, and the recipe says so and why.

## [0.32.0] — 2026-08-11

### Upgrade notes

**Two optional extras raise their floors.** `redis` moves to `>=5.0.1` and
`jwt` to `>=5.3.1`, both from the `.0` of the same minor. Nothing that resolves
today moves; a consumer pinning the exact bottom of either window was getting a
combination that never worked.

### Fixed

- **The `floor` job added in 0.31.0 was not measuring the floor.** uv records
  the resolution mode in the lockfile and silently discards a lock resolved in
  a different mode — "Ignoring existing lockfile due to change in resolution
  mode: `lowest-direct` vs. `highest`". The plain `uv sync` that followed
  `uv lock --resolution lowest-direct` therefore re-resolved at *highest* and
  undid the step before it, so the suite ran against the newest versions while
  the job reported that it had tested the oldest. `uv sync --frozen` and
  `uv run --no-sync` keep the resolve that was just made. The job also pins the
  oldest supported Python instead of taking whatever the runner exposes: the
  claim is about the oldest configuration we support, and leaving it to the
  image makes the answer drift when the image does.

  Only the second half of that job — the base install, which resolves
  `lowest-direct` directly rather than through the lock — was ever measuring
  anything.

- **`redis>=5.0` was a floor that could not work.** The async SSE broker and
  replay buffer call `Redis.aclose()`, which first exists in redis-py 5.0.1.
  Now `>=5.0.1`.

- **`djangorestframework-simplejwt>=5.3` was a floor that could not import.**
  5.3.0 imports `pkg_resources` at module level, so on any environment without
  setuptools — which includes a plain `uv venv` — the `SimpleJWTCookieAdapter`
  fails at import. Now `>=5.3.1`.

- **Two dev-group floors were wrong in the same way.** `fakeredis>=2.20` is
  now `>=2.20.1` (`FakeAsyncRedis`, which the async broker tests import, first
  exists there), and `pytest-cov>=5.0` is now `>=7.0` — this suite spawns
  subprocesses that write their own coverage data, and every pytest-cov before
  7.0 fails to combine them against a current coverage, after the last test has
  passed, so the run prints a green summary and still exits non-zero.

  All four were found by the corrected job on its first honest run. That is the
  job working: none of them was reachable through the lockfile, because the
  lockfile can only ever check the newest.

## [0.31.0] — 2026-08-11

### Changed

- **The `djangorestframework-services` ceiling is gone — the dependency is now
  `>=0.36`, was `>=0.36,<0.37`.** A one-minor window over a sibling package we
  publish ourselves is not a compatibility statement, it is a schedule: every
  upstream release makes this package unresolvable until someone re-cuts it,
  whether or not anything broke. Nine of the sixteen upper bounds across these
  packages had that shape, and none of them has a recorded case of catching a
  real incompatibility — while they have caused four incidents, including a
  security release that was published and unreachable ecosystem-wide, and two
  disjoint windows that resolved *successfully* by silently downgrading a
  consumer past every fix.

  The bound comes off because two measurements replaced it, not because the
  risk was waved away. `upstream-drift.yml` resolves the newest versions
  `pyproject.toml` admits, weekly, and runs the suite against them; the new
  `floor` job below resolves every declared dependency at the bottom of its
  window on every pull request. A ceiling was a guess about which future
  versions break. These are measurements of which ones do.

### Added

- **A `floor` job in `tests.yml`, testing the oldest versions this package
  claims to support.** A floor is a claim about the *oldest* version that
  works, and a lockfile can only ever check the newest — so every gate stayed
  green while a consumer whose own constraints pulled an older dependency could
  get a package that does not import. The job resolves with
  `--resolution lowest-direct`, runs the suite, and then repeats the check on
  the install shape with the fewest constraints of all: the package alone, no
  extras and no dev group, which is what `pip install
  djangorestframework-mcp-server` produces. It is in the `tests-passed`
  aggregate's `needs:`, so it can fail a pull request.

## [0.30.0] — 2026-08-11

### Upgrade notes

**A `FilterSet`'s `OrderingFilter` now owns ordering, and `ordering_fields` is
deprecated.** Registering both on one tool is refused at construction;
registering `ordering_fields` alone still works and warns.

Migrate by moving the field list onto the spec's `FilterSet`:

```python
class InvoiceFilterSet(django_filters.FilterSet):
    ordering = django_filters.OrderingFilter(
        fields=(("created_at", "created"), ("amount_cents", "amount")),
    )
```

...and dropping `ordering_fields=[...]` from the registration call. The public
choices become the enum the model sees, so pick names you are happy to expose.
A spec with no `filter_set` has no other route yet and can keep the old knob.

### Fixed

- **An advertised ordering is now actually applied.** `OrderingFilter`
  subclasses `ChoiceFilter`, so a spec carrying one has always advertised an
  `ordering` enum in the tool's `inputSchema` — while `ordering` sat in
  `RESERVED_POST_FETCH_KEYS` and was stripped from the single mapping that
  served as both the selector's kwarg pool *and* the `FilterSet`'s data. The
  value never reached the filter and nothing applied it: rows came back
  unordered, with no error. The two pools are now separate, so the strip still
  protects a selector declaring `**kwargs` while the `FilterSet` sees the
  arguments whole.

  Requires `djangorestframework-services>=0.36`, where the `filter_data` seam
  reaches the read path.

- **`ordering_fields` can no longer silently overwrite a filter's enum.** The
  two carry different vocabularies under one key — raw ORM paths handed to
  `.order_by()` versus the FilterSet's public choices — so declaring
  `ordering_fields` on a spec whose filter already ordered could *break* an
  ordering that worked. That combination is now refused rather than resolved
  in favour of one side.

## [0.29.0] — 2026-08-11

### Upgrade notes

**`MAX_PAGE_SIZE` now defaults to `100`, down from `500`.** A `paginate=True`
selector tool that was serving 500-row pages to a model that asked for them now
serves 100, with `hasNext` true and `totalPages` recomputed. Nothing errors, and
nothing is silently dropped — the extra rows are on the next page, and the
response says so. If you want the old ceiling back it is one key:
`REST_FRAMEWORK_MCP['MAX_PAGE_SIZE'] = 500`, or `max_page_size=500` on the
binding that needs it.

### Changed

- **The advertised page ceiling and the page the server actually defaults to are
  now the same number.** `MAX_PAGE_SIZE` is not only a clamp: it is stamped onto
  the generated `inputSchema` as `limit.maximum`, so it is the figure the model
  reads before it calls. Meanwhile a call that omits `limit` entirely gets 100
  rows, from the dispatch path's own default. An unconfigured deployment was
  therefore publishing a ceiling **five times** the page it serves by default —
  and publishing a `maximum` is an invitation to take it, so the larger number
  was the likelier one to be asked for. The two agree now.

  **This is not the fix for a page that is too big in bytes.** Rows are a poor
  proxy for payload: a tool with fat serializers can blow a context window at
  ten rows, and 100 does nothing for it. `MAX_RESULT_BYTES` is still the bound
  that counts what the client actually pays.

### Fixed

- **The FilterSet recipe described a `limit` default that does not exist.** It
  said `limit` defaults "to the configured page size", which is wrong in both
  halves: the default is the dispatch path's own `100`, applied only when the
  argument is absent, and there is no setting behind it. The phrase also pointed
  a reader at `PAGE_SIZE` — the *listing* knob for `tools/list` and friends,
  which never reaches a selector tool's `limit`. The same page's sample
  `inputSchema` printed `limit` without the `maximum` the generator always emits.

## [0.28.1] — 2026-08-10

### Fixed

- **`from rest_framework_mcp.contrib.oauth import check_oauth_url_shadowing`
  now works.** The name was in the package's `__all__` with no import beside
  it, so that import failed and `import *` failed outright — for a symbol
  sitting one file away.

- **The documented default of `REQUIRE_TOOL_PERMISSIONS` was the opposite of
  the shipped one.** The settings table said `False`; it has been `True` since
  0.25.0. Two more places still described the old behaviour ("emits
  `UnguardedToolWarning`; set `True` to refuse") when registration is now
  refused by default and `False` is the migration escape hatch.

  **The direction of this one matters.** A reader following the old text
  believed unguarded tools shipped with a warning, when they are refused — so
  the docs described a *laxer* server than the one they had.

- **The three session settings had no rows at all** — `SESSIONS_ENABLED`,
  `SESSION_TTL_SECONDS`, `SESSION_MAX_AGE_SECONDS`. Documented now, including
  why the absolute cap is not really optional: a session's principal binding is
  checked once, at `initialize`, so a sliding idle window alone keeps a revoked
  principal alive for as long as it keeps talking.

- **`build_oauth_urlpatterns` was documented as taking `server` positionally.**
  It is keyword-only, so the copy-paste example raised `TypeError` for the first
  person to run it. Corrected in all three places.

- **Assigning the settings dict replaces it rather than merging**, which was
  only ever said in a troubleshooting tip about tests. It is now stated where
  the dict is configured, along with the same rule one level down: a
  dict-valued setting such as `SERVER_INFO` is taken whole, not deep-merged.

### Added

- **`make docs-check`, wired into CI** — every `python` fence in the docs has
  its imports resolved against the *installed* packages and its calls bound
  against the real signatures.

  **The two checks it now performs are the two ways these docs actually
  rot.** Resolving imports catches a symbol that moved or vanished, including
  in a dependency; binding calls catches an argument the callee cannot accept —
  including one passed **positionally to a keyword-only parameter**, which
  reads perfectly and which a keyword-name check cannot see, because the
  keyword was never written down. That is the `build_oauth_urlpatterns` defect
  above, and it now fails the build.

- **A test asserting every `__all__` name is actually bound**, checked against
  the source rather than the imported module.

  **The runtime version of this check does not work, and quietly.** Importing
  `pkg.thing` binds `thing` as an attribute of `pkg`, so once anything has
  imported the submodule, `hasattr(pkg, "thing")` is `True` while
  `from pkg import thing` hands back a *module* that is not callable. The first
  draft passed with the OAuth defect reintroduced; the static check fails on it.

## [0.28.0] — 2026-08-10

### Security

- **An authenticated caller with no `pk` is refused instead of sharing the
  `"anonymous"` principal.**

  Sessions and tasks are owned by a principal id derived from the resolved
  user's primary key. A backend that resolves a *real* caller to something
  without one — a service-account object, a JWT-claim wrapper, a custom
  principal class — fell through to `"anonymous"` alongside every other such
  caller. **Two distinct authenticated callers on one principal can each
  present the other's session id and be served**, and tasks use the identical
  ownership comparison, so the same merge hands over another caller's task
  results.

  **It failed silently and looked like it was working**: every request
  succeeded, every session resolved, and the only symptom was that isolation
  was not there. Hence a raise rather than a degrade — a backend hitting this
  is one line from correct. Give the resolved user a `pk`, or return
  `AnonymousUser` and mean it.

  **Deliberate anonymity is unaffected.** `AnonymousUser` (from a permissive
  backend such as `AllowAnyBackend`) and a token with no user at all still map
  to the shared `"anonymous"` principal — nobody was identified, so sharing is
  the honest answer. What is refused is the *ambiguous* middle: a user object
  declaring neither a primary key nor `is_authenticated is False`.

  **Related to the 0.26.0 auth fix by mechanism, not coincidence.** The
  un-awaited coroutine that authenticated every caller also had no `pk` — so
  the same misconfiguration that let everyone in *also* collapsed them onto one
  session namespace. Two findings, one incident.

## [0.27.0] — 2026-08-10

### Fixed

- **Requires `djangorestframework-services>=0.35,<0.36`** (was
  `>=0.34.0,<0.35`), which makes this package co-installable with
  `djangorestframework-pydantic-ai` again.

  **The two ranges had gone disjoint.** PAI 0.13.0 moved to
  `drf-services>=0.35` for the `unguarded_specs` predicate while this package
  stayed on `<0.35`, so **every project depending on both was unsatisfiable** —
  `django-pydantic-agent[drf-mcp,spec-tools]` and
  `django-ag-ui[drf-mcp,spec-tools]` could not resolve at all. Nothing was
  wrong with either package; the pair was.

  **A pure floor move, with no adaptation.** drf-services 0.35 is additive
  (`unguarded_specs`, `combine_progress`, a `progress_reporter` spec field and
  the matching view hooks) and this package uses none of it — the full suite is
  green against 0.35 untouched. The window moves rather than widening to
  `>=0.34,<0.36` so the ecosystem sits on exactly one drf-services minor, which
  is what keeps a pairing that resolves cleanly from behaving differently.

## [0.26.0] — 2026-08-10

### Security

- **An async auth backend on the sync transport authenticated every caller.**
  `docs/recipes/async-auth-backend.md` documented an `async def authenticate`
  and claimed the sync view bridged it via `async_to_sync`. No such bridge
  exists: the sync transport called the backend and got back an un-awaited
  coroutine, which is *truthy*, so the `token is None` check that produces the
  `401` passed and every request — credentials or not — was served as
  authenticated (as the shared `"anonymous"` principal, since a coroutine has
  no `pk`). Anyone who followed that recipe and mounted `server.urls` was
  running an open endpoint that reported nothing wrong.

  The sync transport now inspects what `authenticate` returned and raises
  `ImproperlyConfigured` when it is awaitable, naming both remedies: mount the
  server under `server.async_urls`, or make the backend a plain `def`. Existing
  installs are covered by the raise, not by the doc fix — an already-deployed
  copy of the recipe keeps working exactly as before until it is upgraded, at
  which point it fails loudly instead of silently open.

- **The same defect, swept as a class: every consumer-supplied hook a sync path
  consults now refuses an awaitable.** The auth backend was one instance of a
  general shape — *a coroutine is truthy and is never `None`, so any hook
  written `async def` has its return value silently read as a yes*. Four more
  sites had it:

  | Hook | What an `async def` did before |
  | --- | --- |
  | `MCPPermission.has_permission` | `not result` was `False` → **every caller granted**, on `tools/call`, `prompts/get`, selector and chain dispatch |
  | `is_listable` / `has_permission` at list time | → **every binding listed**, regardless of the caller |
  | `MCPRateLimit.consume` | `retry_after is not None` was `True` → every call denied, with the coroutine object as `retryAfter` |
  | `SessionStore.create` / `owner` / `destroy` on the **sync** transport | A coroutine's `repr` handed out as a session id; ownership matching nothing; a `destroy` discarded |

  **The permission sites failed open on ASGI too.** Permissions are reached
  through the aggregate `check_permissions`, which the async transport bridges
  with `acall` — bridging *that* function, not the hooks it calls. So an
  `async def has_permission` was as un-awaited under ASGI as under WSGI. The
  `acall` docstring listing "custom permissions" among its bridged
  collaborators was wrong, and is corrected.

  **Permissions and rate limiters are now synchronous by contract on both
  transports** (their Protocols always declared them so) and raise
  `ImproperlyConfigured` naming the offending class, the remedy, and what
  continuing would have cost. Use `asgiref.sync.async_to_sync` inside the
  method if it needs to await. **Session stores keep async support** — that is
  the deliberate async seam — but only under `server.async_urls`; the sync
  transport now names the mounting instead of misbehaving quietly.

  No supported configuration changes behaviour: every one of these was already
  broken, three of them invisibly and in the direction of "allow".

### Added

- **Progress works inside a task.** `run_task` never seeded a reporter, so a
  service executed as a task got the no-op and every `progress(...)` call was
  discarded — silently, and specifically for the long-running work that tasks
  exist to carry. The inline path was fine, but its reporter needs a live
  connection and a worker has none.

  Reports now land on the task record: `TaskRecord` gains `progress` / `total`,
  and the wire `Task`'s `statusMessage` carries the rendered form
  (`"Exporting (142/500)"`) that a polling client reads through `tasks/get`.
  Push becomes poll; the service body is byte-identical either way, which is
  the point.

  The numbers stay server-side — the protocol `Task` has no numeric field — and
  `meta` is dropped on this path, since a task has no notification to put it
  in. A finished task is never rewritten, no `notifications/tasks` is published
  per tick, and a store that is down does not take the operation with it.

  The **sync** dispatch path now forwards `context.progress`, which it
  previously skipped on the grounds that there was "no stream to report on" —
  true of the connection, false of the worker that runs it.

### Fixed

- **Docs: `async-auth-backend` recipe corrected.** It now directs async
  backends to `async_urls` only and drops the bridge claim. Two further defects
  in the same example are fixed: `protected_resource_metadata` returned a plain
  `dict`, which raises `AttributeError` in the PRM ViewSet (it calls
  `.to_dict()`), and `TokenInfo(user=<sub string>)` gave every caller the shared
  `"anonymous"` principal, so session and task ownership isolation collapsed.
  The same `dict` return is corrected in `docs/async.md`.

## [0.25.0] — 2026-08-05

### Upgrade notes

Two behaviour changes need a decision before you deploy.

1. **`REQUIRE_TOOL_PERMISSIONS` now defaults to `True`.** A tool registered with
   neither `spec.permission_classes` nor a per-binding `permissions=[...]`
   **raises** at registration — which is import time, so it fails the deploy
   rather than a request. Declare permissions, or set
   `REST_FRAMEWORK_MCP['REQUIRE_TOOL_PERMISSIONS'] = False` to migrate
   gradually. If your tests assign `settings.REST_FRAMEWORK_MCP = {...}`, that
   **replaces** the dict rather than merging, so a project-level opt-out
   disappears inside them — add the key to those literals too.
2. **A request with no `Mcp-Session-Id` now returns `400`, not `404`.** Anything
   asserting `404` for a *missing* header needs updating. `404` still means what
   the spec says it means: an id arrived that the server will not honour.

### Added

- **`SESSIONS_ENABLED` — serve the legacy era without session state.** With it
  off, the `initialize`-handshake era mints no `Mcp-Session-Id`, requires none,
  ignores a stale one a client is still echoing, and answers `405` to the SSE
  `GET` and the session `DELETE`.

  **A conformant mode, not a relaxation.** Both legacy revisions say a server
  *"MAY assign a session ID at initialization time"* and make the client's duty
  to echo one conditional on it having arrived, so a server that never assigns
  is never sent one.

  It exists because a session is state, and state expires, gets evicted, and
  dies with a deploy — each reaching the client as a `404` whose documented
  remedy is to re-`initialize`, which not every client does. **Every other fix
  for that failure class belongs to the client vendor**; this one does not. The
  modern (`2026-07-28`) era is stateless already and ignores the setting.

  What you give up: server-initiated messaging on the legacy era, since the
  session id is what addresses a client's SSE channel.

- **`SESSION_TTL_SECONDS` / `SESSION_MAX_AGE_SECONDS`**, replacing a
  module-private 24-hour constant that could only be changed by subclassing the
  store. The TTL is now an **idle** window that restarts on every successful
  read, so a session in continuous use never lapses — previously a connector
  talking every minute still died on the 24-hour mark. The absolute cap is not
  decorative: the principal binding is checked once, at `initialize`, so an
  unbounded sliding window keeps a *revoked* principal alive for as long as it
  keeps talking (the argument `SUBSCRIPTION_MAX_SECONDS` already makes).

  Neither window can promise more than the cache underneath. A Redis
  `allkeys-lru` policy evicts session keys before any TTL, indistinguishably
  from expiry.

- **`MCP-Error` response header** on transport-level rejections
  (`session-missing` / `session-unknown`). Ours, not the spec's. The statuses
  the spec mandates are undiagnosable in production: a `404` from a dead session
  and a `404` from a load balancer are identical to a client, and the JSON-RPC
  body that would separate them often never reaches a human — clients commonly
  log `${status} ${statusText}`, and **HTTP/2 has no reason phrase**. The header
  carries strictly less than the body, so it leaks nothing: unknown-id and
  wrong-principal share one slug, preserving the no-ownership-oracle property.

- **Logging, under the `rest_framework_mcp` namespace.** The package previously
  emitted **nothing** — no `getLogger`, no `logger` call anywhere. Session
  rejections, auth failures and every outbound bound now log at `WARNING`;
  `initialize` and era selection at `INFO`; dispatch timing at `DEBUG`.

  Session rejections name **which** condition fired, even though the response
  merges them: the no-oracle rule constrains the wire, and an operator reading
  logs is not the adversary it protects against. Tokens and tool payloads are
  never logged; session ids appear as a short prefix.

- **`check_oauth_url_shadowing()`** — django-oauth-toolkit 3.4.0 serves its own
  `register/` and `.well-known/oauth-authorization-server`, and Django resolves
  first-match, so mounting DOT's urls before `build_oauth_urlpatterns(...)`
  means DOT answers them silently. A function you call rather than a Django
  system check, because this package is a library with no `AppConfig`.

- **`authorize_path` / `token_path` / `registration_path`** on
  `DjangoOAuthToolkitBackend`, for a project that mounts DOT somewhere other
  than `/oauth/`.

### Changed

- **Unguarded tools are refused by default** — see the upgrade notes. The
  asymmetry that justifies it was already in the warning's own text: DRF
  viewset-level and `REST_FRAMEWORK` default permission classes do **not** apply
  over MCP, so the habit the framework trains produces an open tool. The raise
  now names the way *out*; reusing the warning's "set the flag to make this an
  error" told you to enable the setting that had just fired.

- **The structured-output coupling is checked at registration**, not on the
  first `tools/call`. Deliberately *not* checked on the global settings pair:
  server-wide `INCLUDE_OUTPUT_SCHEMA=True` with `INCLUDE_STRUCTURED_CONTENT=False`
  is legal precisely when every binding overrides the content back on.

- **The bundled examples now declare permissions explicitly** (`AllowAny`, which
  says "deliberately open" out loud in a demo). They were registering unguarded
  tools — the pattern the new default exists to stop, in the code consumers copy.

- **Floor raised to `djangorestframework-services>=0.34`** for `preconditions`.

### Fixed

- **A request with no `Mcp-Session-Id` returned `404` instead of `400`.** The
  spec reserves `404` for a request *"containing that session ID"* after the
  server dropped it, and says a server requiring a session "SHOULD" answer a
  header-less request with `400 Bad Request`. Both rendered `404`.

  The wrong code also routed clients into the wrong compatibility branch:
  `2025-11-25` lists `400` among the statuses that send a client down the
  legacy-fallback path. Splitting the two leaks nothing — a caller already knows
  whether it sent a header — and unknown-id versus wrong-principal stay merged,
  which is the pair that would otherwise be an ownership oracle.

## [0.24.1] — 2026-08-02

### Changed

- **Requires `djangorestframework-services>=0.33`** (was `>=0.32`).

  **Take this one promptly — 0.33.0 closes an authorization bypass that this
  package is the most exposed surface for.** In drf-services 0.32 and earlier a
  spec's nested target resolution (`instance_selector_spec` /
  `collection_selector_spec`) built its kwarg pool without stripping the reserved
  dispatcher seeds, so a caller-supplied `user` key outranked the authenticated
  one in the pool that decides **which row gets mutated** and **which set gets
  bulk-deleted**. Over MCP those params are the tool call itself, which makes
  this directly client-reachable here rather than config-dependent.

  Exploitation needs a selector that declares a reserved seed name (`user` being
  the realistic one). If any of your specs do, treat this as urgent.

  The floor moves to `>=0.33` rather than merely widening the ceiling: a pairing
  that resolves cleanly and leaves the bypass live is exactly what a resolver
  cannot see.

  No source changes here — the fix arrives through `dispatch_spec` unchanged, and
  the full suite passes against 0.33.0 untouched.

## [0.24.0] — 2026-07-31

This release makes the package **dual-era**: one endpoint that serves both the
`2026-07-28` stateless revision and the `2025-11-25` / `2025-06-18` handshake
era, with the request itself deciding which. Everything the new revision put in
place of the machinery it removed is here — `server/discover` for the retired
handshake, `subscriptions/listen` for the retired standalone SSE stream, tasks
for work that outlives a request, and elicitation for the retired
server-initiated request.

It also closes the gaps the same audit found in the era already being served:
non-text content, argument completion, icons, streaming progress, and four
JSON-RPC error codes that did not match the spec.

**Read *Changed* before upgrading.** The error-code corrections change wire
values existing clients may be matching on — in particular a permission denial,
which was being answered with the spec's "resource not found" code.

### Added

- **Elicitation — a tool can stop and ask the user something.** A service raises
  `AdditionalInputRequired`, the client puts the question to the user, and the
  answer comes back as an ordinary tool argument:

  ```python
  def delete_rows(*, data):
      if len(rows_matching(data)) > 100 and not data["confirmed"]:
          raise AdditionalInputRequired(
              f"{len(doomed)} rows match. Confirm to proceed.",
              schema={"confirmed": {"type": "boolean"}},
          )
  ```

  The call answers with `resultType: "input_required"` instead of a result, and
  the client **retries the original call** carrying the answers. That is a
  *success* with a second legal shape, inside a `200` — a client treating a
  non-`complete` `resultType` as a failure will never retry.

  **Nothing is held between the two requests.** This is what `2026-07-28` put
  in place of server-initiated requests, so the retry may land on a different
  process entirely. The service is not resumed; it runs again from the top with
  the answer present, which is a reason to raise early and keep `atomic=True`.

  `requestState` travels through the client and comes back, so it is treated as
  attacker-controlled: HMAC-signed and bound to the authenticated principal, to
  a digest of the original call, and to a `INPUT_REQUEST_TTL_SECONDS` expiry.
  Every failure — bad signature, wrong principal, wrong call, too late —
  answers the same way, by ignoring the state and asking again.

  A client that did not declare the `elicitation` capability is **not** given a
  protocol error. It gets an ordinary `isError` result carrying the message and
  the requested schema, which a model can act on by supplying the argument
  itself. Same for a legacy-era client and for a task worker replaying a call
  with nobody to ask.

  Service tools only. A chain tool degrades to an ordinary error rather than
  asking, because re-running the call would re-run its earlier steps; a selector
  is a read. `tools/call` only — the spec also permits this on `prompts/get`
  and `resources/read`, which dispatch bare callables with no failure channel.
  Form mode only; sampling and roots are Deprecated in this revision and are not
  built.

- **`invalidates=` — a mutation tool announcing its own changes.** Declared at
  registration, next to every other per-tool knob, because a spec running
  through this server *is* the moment a resource changed and the server is
  already standing in the call path:

  ```python
  server.register_service_tool(
      name="invoices.create",
      spec=ServiceSpec(service=create_invoice),
      invalidates=("invoices://{pk}", "invoices://"),
  )
  ```

  Same `{var}` syntax as a resource's `uri_template`, rendered against the
  result merged with the call's arguments — result wins, since after a write it
  is authoritative, while the arguments are the only source for a delete whose
  result carries nothing. A template that cannot render is dropped rather than
  raised: the write has already committed, and failing the call over a
  formatting mistake would report failure for work that succeeded.

  **Published after the transaction commits**, via `transaction.on_commit`.
  Announcing from inside `atomic()` tells a subscriber to re-read something that
  may still roll back — and a wrong notification is worse than a missed one,
  since the next read recovers a miss but not a lie. The async transport routes
  the announcement through the thread that did the write, because Django
  connections are thread-local and checking the transaction from the event loop
  reads a different connection that reports none open.

  A call that came back `isError` announces nothing; selector tools do not
  accept the kwarg at all (a read changes nothing); a chain fires once after the
  whole chain succeeds rather than per step.

  **Its boundary is real and stated in the docs**: it fires for calls that go
  through this server and nothing else. `MCPServer.notify_resource_updated` is
  the trigger for everything else, which is why it exists rather than being an
  afterthought.

- **Server-pushed notifications — `subscriptions/listen`.** A client opens a
  long-lived POST stream and names what it wants to hear about: resource URIs,
  and any of the three `list_changed` kinds.

  ```python
  server = MCPServer(
      name="invoices",
      subscription_broker=RedisSubscriptionBroker(Redis.from_url("redis://…")),
  )
  # …then, after the write commits:
  await server.notify_resource_updated(f"invoices://{invoice.pk}")
  ```

  **`resources/subscribe` is not the legacy twin of this method — it is
  *replaced* by it.** The GA schema folds resource subscriptions into a
  `resourceSubscriptions` field of the `subscriptions/listen` filter, alongside
  the list-changed kinds, and says so in as many words: *"replaces the former
  `resources/subscribe` RPC"*. The legacy RPC is **not** implemented — see
  *Fixed*, which is where that decision and the advertisement it corrected are
  recorded.

  What the shape buys, and what it costs:

  - **Every notification type is opt-in**, which the spec makes a MUST. Opt-in
    is enforced by what the stream attaches to rather than by filtering on the
    way out, so a type nobody asked for has no path to the wire.
  - **The first frame is always the acknowledgement**, reporting the subset the
    server agreed to honour — so a client learns immediately that something it
    asked for will never arrive, rather than waiting for it.
  - **Subscribing to a resource needs the same permission as reading it.**
    Otherwise a subscription is a side channel around `resources/read`: *when*
    something changes is often the more sensitive signal. A refused entry is
    dropped from the acknowledgement rather than erroring, which also stops the
    endpoint becoming an oracle for which resources exist.
  - **One occupied ASGI worker per open subscription**, inherent to the wire
    format. Bounded by two new settings: `SUBSCRIPTION_MAX_SECONDS` (which is
    also the re-authorization interval, since permissions are checked once when
    a subscription opens) and `MAX_CONCURRENT_SUBSCRIPTIONS` (per worker;
    without it an authenticated caller can exhaust the pool by opening streams
    in a loop).
  - **A subscription granted nothing is acknowledged and closed**, not held
    open to deliver silence.
  - **`taskIds` closes the loop with the tasks extension** — a client subscribes
    to a task and stops polling `tasks/get`. Every status change pushes
    `notifications/tasks` carrying the *whole* task, identical to what
    `tasks/get` would have returned, so a missed notification costs nothing.
    Watchable only by the principal that created the task. Asking for
    `taskIds` without declaring the tasks extension is a JSON-RPC error rather
    than a quiet omission — the spec's one exception to dropping refused
    entries, and not an oracle, since it turns on what the client declared
    rather than on the ids it named.

  `SubscriptionBroker` is a new collaborator rather than a widening of
  `SSEBroker`: that one keys on session with a single subscriber, and both
  assumptions fail here (a notification is addressed to a topic, and several
  clients legitimately watch the same resource). **There is no default
  broker.** A server that quietly got the in-process one would advertise support
  and then silently deliver nothing as soon as a second worker existed;
  `RedisSubscriptionBroker` (in the `[redis]` extra) is the deployable one.

- **Tasks — durable handles for work that outlives its request.** The
  `io.modelcontextprotocol/tasks` extension. A task-eligible tool answers
  `tools/call` with a handle instead of a result; the work runs in a queue
  worker and the client polls `tasks/get` until the status is terminal.
  Modern-era only, since the client declares support on every request.

  Two arguments to wire it up. The executor seam is one method taking one
  string, so Celery, RQ, Dramatiq or a thread pool all satisfy it and none of
  them is imported here:

  ```python
  @shared_task
  def run_mcp_task(task_id: str) -> None:
      server.run_task(task_id)


  class CeleryExecutor:
      def enqueue(self, task_id: str) -> None:
          run_mcp_task.delay(task_id)


  server = MCPServer(name="invoices", task_executor=CeleryExecutor())
  ```

  Passing `task_executor=` also builds a `DjangoCacheTaskStore` namespaced to
  the server; `task_store=` overrides it. `InMemoryTaskStore` is for tests and
  `runserver` only — a task is created on a web worker and finished somewhere
  else, so an in-process store writes the result where the poll cannot see it
  and every poll answers "unknown task".

  Which tools are eligible is declared at registration, because the extension
  makes the **server** the sole decider and gives the client no way to ask:

  | `task_policy` | Declaring client | Non-declaring client |
  |---|---|---|
  | `FORBIDDEN` (default) | inline | inline |
  | `OPTIONAL` | task handle | inline |
  | `REQUIRED` | task handle | `-32021` |

  `FORBIDDEN` is the default, so **every tool registered before this behaves
  exactly as it did**.

  Also lands `tasks/get` / `tasks/update` / `tasks/cancel`, `Mcp-Name` mirroring
  `params.taskId` on all three, and an `extensions` field on
  `ServerCapabilities` — advertised only when the server has both a store and an
  executor, since half the machinery hands out handles nothing will ever run.

  **The extension document's error code is not the one emitted.** It prints
  `-32003` while annotating it `MISSING_REQUIRED_CLIENT_CAPABILITY` — the
  constant the ratified core schema allocates as **`-32021`**. It is a carry-over
  from when tasks were part of the core protocol, and `-32003` now sits inside
  the `-32000`–`-32019` band the core spec reserves for implementations and
  promises never to define codes in. It is also one of the two codes this
  package burned, so a client from an older release would read it as "not
  found". `-32021` is emitted.

  A few behaviours worth knowing, each of which is a spec requirement or a race
  that would otherwise bite:

  - **Permissions run twice, rate limits once.** The permission stack runs
    before the task is created, so a denied call never reaches the queue and
    still gets its `403`; it runs again in the worker against a token rebuilt
    from the stored scopes. Rate limits are charged only at creation — consuming
    a quota is a side effect, and charging it again on replay would halve every
    configured limit.
  - **A tool error completes the task.** `failed` is for the task machinery
    breaking. A `ServiceError` produces a well-formed result carrying
    `isError: true`, which is a task that finished — the spec says so in as many
    words, and getting it backwards would hide every tool error behind a status
    the client reads as "the server broke".
  - **There is no `tasks/list`.** Deliberate in the spec: without sessions there
    is nothing to scope a listing by. Ids carry 32 bytes of entropy and
    ownership is checked on top; an id belonging to another principal answers
    identically to one that never existed, so no endpoint becomes an oracle for
    which ids are real.
  - **A queue that is down fails the task, not the request.** The record is
    durable before `enqueue` is called, so a broker failure comes back as a
    handle already in `failed` with the reason in `statusMessage`, rather than
    an error that leaves the client with no handle and a record stuck in
    `working`.
  - **Redelivery runs the work once.** Tasks are claimed as the worker starts.
    Best-effort rather than a lock — an idempotent service is still worth
    writing.

- **Streaming progress for long-running tools.** A service or selector declares
  `progress` and reports as it goes; the client sees it happen:

  ```python
  def export_invoices(*, data, progress):
      for index, row in enumerate(rows):
          write(row)
          progress(index + 1, total=len(rows), message="writing rows")
  ```

  Registration is unchanged — `progress` is a kwarg-pool seed from
  `djangorestframework-services` 0.30 (the new floor), so it arrives like
  `request` and `user`, and the same service runs unchanged where nobody is
  listening.

  **The client opts in** with a `progressToken` in the request's `_meta`. That
  token is the only trigger: with it the response becomes `text/event-stream`
  carrying `notifications/progress` frames followed by the result; without it,
  a single JSON object as before. A stream whose only event is the final
  response costs a connection and buys nothing.

  **Era-independent** — `_meta.progressToken` sits in the same place in
  `2025-11-25` and `2026-07-28`, so a legacy client streams on the same terms
  as a modern one. **ASGI only**: a sync WSGI view cannot yield mid-dispatch,
  so `server.urls` keeps answering `application/json`, which stays spec-legal.

  What the server does with the reports:

  - **Non-increasing reports are dropped.** The spec makes increase a MUST, so
    forwarding one would put this server in violation on the service's behalf.
  - **Frames are capped** by the new `MAX_PROGRESS_NOTIFICATIONS` (default
    1000). The spec asks both parties to rate-limit, and a per-row reporter
    over a large table is a flood. Past the cap reports are dropped — the
    dispatch is untouched and the result still arrives.
  - **`meta` rides in the notification's `_meta`**, so `message` stays prose.
  - **Closing the stream cancels the request**, which is what `2026-07-28`
    means by cancellation-by-disconnect. It cancels the await, not the work —
    a thread parked in a driver's socket read is not interruptible by asyncio,
    the same caveat `DISPATCH_TIMEOUT` carries.

  **Permissions are checked before the stream opens.** A streaming response
  commits its status before the handler runs, so a denial found inside could
  only ride as an in-stream error inside a `200` — losing the `403` the
  authorization spec makes normative. The permission stack runs once at the
  transport first, so a denied call still gets `403` and a `WWW-Authenticate`
  challenge. Rate limits stay inside the handler: consuming one is not
  idempotent, and a rate-limit rejection was already a `200`.

  `X-Accel-Buffering: no` is set on the response stream, as it already was on
  the session stream — without it nginx buffers the whole body and flushes on
  close, defeating the point.

- **The `2026-07-28` transport, served alongside the existing one.** This
  package is now **dual-era**: one endpoint, two protocol revisions, and the
  request itself decides which. A request carrying
  `params._meta["io.modelcontextprotocol/protocolVersion"]` is served
  statelessly under the modern rules; its absence means legacy, which behaves
  exactly as before.

  **The `_meta` marker is the discriminator, not the header and not the
  method.** Legacy clients have sent `MCP-Protocol-Version` since `2025-06-18`,
  and most methods exist in both eras — the per-request metadata is the only
  thing that appears in one era and not the other.

  On the modern path there is no session: none is looked up, none is minted,
  and an `Mcp-Session-Id` a client sends anyway is ignored rather than
  rejected. `GET` and `DELETE` answer `405` to a caller naming a modern
  revision, since the standalone SSE stream and session termination were both
  removed there.

  **Keeping legacy is deliberate.** Legacy clients have no fall-forward
  mechanism: dropping the era would strand every client that has not migrated
  with nothing but an error string to act on. The cost is one branch at the
  transport edge — everything below it stays era-agnostic, with the single
  exception noted under *Changed*.

- **Header validation on the modern path.** `Mcp-Method` and, for
  `tools/call` / `resources/read` / `prompts/get`, `Mcp-Name` are required and
  checked against the request body; so is `MCP-Protocol-Version` against the
  `_meta` version. A mismatch is `400` with `-32020`.

  Not pedantry: the transport mirrors body fields into headers so gateways can
  route without parsing JSON, and that is only safe while the two agree — a
  load balancer routing on the header while the server executes the body is a
  confused deputy. `Mcp-Name` values arriving in the Base64 sentinel form
  (`=?base64?…?=`, which clients must use for anything that would not survive
  as a plain ASCII header) are decoded before comparison.

  Two more status codes are now normative and implemented: an unknown method is
  `404` with a `-32601` body, and an unsupported version is `400` with `-32022`
  carrying `supported` and `requested`. Both are what a client reads to tell a
  modern MCP endpoint from a legacy one, which is why the JSON-RPC body matters
  as much as the status.

- **`server/discover`.** The `2026-07-28` revision's replacement for the
  `initialize` handshake, and a **MUST** for servers on that revision: same
  three answers — supported versions, capabilities, identity — but as an
  ordinary request. Nothing is negotiated and no state is created, so a client
  may call it, repeat it, cache it, or skip it entirely.

  **Answered without a session or a protocol-version header**, alongside
  `initialize`. A modern client sends it precisely because it has nothing yet:
  gating discovery behind a session would leave it reachable only by clients
  that did not need it, and requiring a version header would mean naming a
  version in order to find out which versions are supported. It does not
  *mint* a session either — discovery creating state would defeat the point.

  Answered in both protocol eras: the versions, capabilities and identity it
  reports are properties of the server, not of the era asking. `serverInfo`
  rides in `_meta` under the spec's reserved key rather than at the top level,
  which is the spec's way of saying it is self-reported and not something a
  client should make decisions from.

- **`resultType`, `ttlMs` and `cacheScope` on results.** `resultType:
  "complete"` is stamped into every result in the JSON-RPC response envelope —
  one place, so no handler can forget it — which is a MUST from `2026-07-28`
  and inert before it, since older clients are told to read an absent
  `resultType` as `complete`.

  The six cacheable results (`server/discover` and the five list/read methods)
  now carry `ttlMs` and `cacheScope`. Two new settings:
  `CATALOG_CACHE_TTL_MS` (default 60 s) for catalogs, and
  `RESOURCE_CACHE_TTL_MS` (default `0` — live data) for `resources/read`,
  overridable per resource with `cache_ttl_ms=`. Worth setting on anything
  genuinely static: an interactive view changes only on deploy, and hosts
  prefetch views before any tool call.

  **`cacheScope` is derived, never configured.** `public` licenses a shared
  gateway or proxy to serve one response *across authorization contexts*, so a
  per-caller result labelled `public` is a cross-tenant disclosure with a cache
  in front of it — not the kind of thing a settings knob should be able to get
  wrong. A listing filtered by `FILTER_LISTINGS_BY_PERMISSIONS` is `private`,
  an unfiltered one is `public`, and a resource body — produced by a selector
  for this caller — is always `private`.

- **The spec-reserved error codes** `-32020 HeaderMismatch`,
  `-32021 MissingRequiredClientCapability` and `-32022
  UnsupportedProtocolVersion` are now named in `JsonRpcErrorCode`. Nothing
  emits them yet; they arrive with header validation.

- **Non-text tool results and binary resources.** Every content block the MCP
  spec defines is now reachable, where before only `text` was ever constructed
  and `ResourceContents.blob` existed as a field nothing populated.

  - `ResourceEncoding.BLOB` — the selector returns `bytes`, the body is
    base64-encoded into `blob`. What a PDF, an image or a generated spreadsheet
    needs.
  - `content_kind=` / `content_mime_type=` on tool registration, taking
    `ToolContentKind.IMAGE` / `AUDIO` / `RESOURCE_LINK` (default `TEXT`, which
    is today's behaviour unchanged). Declared, never sniffed — a base64 string
    and a text body are indistinguishable by inspection, so guessing would
    silently change behaviour for a tool that already returns one.
  - `ToolContentBlock` gained typed constructors for all five block types
    (`text_block` / `image` / `audio` / `resource_link` / `embedded_resource`)
    and the fields the non-text ones need. `PromptMessage.block()` reuses them,
    so a prompt can carry an image without a parallel vocabulary.

  `RESOURCE_LINK` is the one worth reaching for: the tool returns
  `{"uri": …, "name": …}` (or a list of them) naming resources this server's own
  `resources/read` already serves, so nothing large rides on the tool-result
  path and the client fetches only what it decides it wants. It keeps
  `structuredContent` — the links *are* JSON — while media kinds carry neither
  `structuredContent` nor `outputSchema`, since neither can describe a PNG.
  Declaring a media kind alongside either is refused at registration rather
  than ignored at dispatch.

- **`completion/complete` — argument autocompletion.** Register a completer per
  prompt argument or URI-template variable with `completions={"language": …}`;
  clients offering a dropdown while a user types now get suggestions.

  Completers run through the same kwarg-pool dispatch as everything else, so
  one declares whichever of `value` (the text typed so far), `arguments`
  (siblings the client has already resolved, also spread by name), `request`
  and `user` it needs. Return any iterable — list, generator, queryset: the
  handler slices to the spec's cap of 100 and reports `hasMore` rather than
  draining it, so a queryset reads 101 rows instead of the table.

  **Completion runs the binding's `permissions` and `rate_limits`.** Without
  that, a resource a caller may not read would still answer "which ids exist?"
  one keystroke at a time. A completer keyed to an argument the binding does
  not have is refused at registration — otherwise the failure is an empty
  dropdown with nothing in the logs.

- **`icons` and `websiteUrl`.** `icons=` on every registration method and on
  `MCPServer`, emitted in `tools/list`, `resources/list`,
  `resources/templates/list`, `prompts/list` and `serverInfo`; `website_url=`
  and a settings-only `description` on the server's own identity. `SERVER_INFO`
  accepts all of them.

  `Icon.src` must be `https:` or a `data:` URI, checked at construction:
  clients are *required* to reject anything else, so an `http://` icon is not a
  worse icon — it is one the user never sees, with nothing in the logs to say
  why.

- **`client_id_metadata_document_supported` in authorization-server metadata,**
  sourced from django-oauth-toolkit's `CIMD_ENABLED` (3.4.0+, feature-detected
  — the `[oauth]` floor is unchanged, and CIMD is opt-in even on 3.4). Client ID
  Metadata Documents sit *above* Dynamic Client Registration in the spec's
  registration priority order, and DCR is now deprecated: a server that supports
  CIMD but stays silent about it sends every client down the deprecated path for
  no reason.

- **`application_type` accepted on dynamic client registration.** Validated
  against OIDC's `native` / `web` and echoed in the registration response. MCP
  clients are required to send it — an OIDC authorization server derives
  redirect-URI constraints from it, and an omitted value defaults to `web`,
  which conflicts with the `localhost` redirect URIs a desktop or CLI client
  needs. It was previously dropped in silence. This server validates and
  echoes but does **not** enforce those constraints: it is not acting as an
  OIDC provider, and the spec says non-OIDC servers safely ignore the
  parameter.

### Changed

- **The `djangorestframework-services` floor moves to `>=0.32,<0.33`,** from
  `>=0.29.0,<0.30`. Two of the sister releases it crosses are load-bearing here.

  **0.30** added the `progress` kwarg-pool seed, without which a service had no
  channel back to the transport at all: `dispatch_spec` took a fixed argument
  list and the reserved pool-seed set is owned upstream. One consequence lands
  here — **a `UrlKwarg` or `QueryParam` named `progress` is now refused at
  registration**, since the name is reserved.

  **0.32** added `AdditionalInputRequired`, the exception a service raises to
  say what input it still needs, which the elicitation surface above is built
  on. It also fixes an `AttributeError` that made **every OPTIONS request** to a
  spec-backed viewset an unhandled 500 — worth knowing even though this package
  mounts no viewsets, because a CORS preflight is an OPTIONS request, so any
  project pairing this package with `ServiceViewSet` over HTTP was exposed.

  (0.31, in between, adds only a consumer-owned `metadata` mapping on the specs,
  which nothing here reads.)

- **`PROTOCOL_VERSIONS` now defaults to `["2026-07-28", "2025-11-25",
  "2025-06-18"]`.** One list across both eras — a version belongs to exactly
  one, and splitting the setting would let a project configure a
  contradiction. `server/discover` reports the whole list; each era validates
  against its own half.

  **`initialize` never offers a modern version**, whatever sits at the head
  of the list. It is a legacy-era method — it does not exist in `2026-07-28` —
  so answering a handshake with that revision would hand the client a protocol
  whose very next request this transport would refuse.

- **`resources/read` answers a missing URI by era**: `-32002` for a legacy
  caller, `-32602` for a modern one. The only place the two eras disagree on a
  wire value, and it cannot be collapsed — the revision that retired `-32002`
  also told clients to keep *recognising* it, so neither value is safe to send
  to both.

- **Breaking: JSON-RPC error codes now match the MCP spec.**

  | Condition | Was | Now |
  |---|---|---|
  | `resources/read`, unknown URI | `-32003` | **`-32002`** (+ `data.uri`) |
  | `tools/call`, unknown tool | `-32004` | **`-32602`** |
  | `prompts/get`, unknown prompt | `-32003` | **`-32602`** |
  | Permission denied | `-32002` | **`-32006`** |

  The last row is the reason the others could not wait. `-32002` is the spec's
  code for "Resource not found" — and the one legacy code the `2026-07-28`
  revision singles out for clients to keep recognising — while this package was
  spending it on permission denials. A spec-following client read every denial
  as a missing resource. The HTTP status on a denial is unchanged (`403`, with
  the same `WWW-Authenticate` challenge), which is what a client should be
  acting on.

  `-32003` and `-32004` are now **burned**: they are not reused for anything
  else, because a client written against an older release still reads them as
  "not found" and "unknown tool". `JsonRpcErrorCode.TOOL_NOT_FOUND` is removed;
  `RESOURCE_NOT_FOUND` remains, renumbered.

- **Capabilities are advertised only when the server can answer them.**
  `tools` and `resources` were advertised unconditionally while `prompts` was
  conditional, so a server with no resources still told every client to go and
  call `resources/list`. All four now follow one rule, sourced from the
  registries. Deliberately *not* filtered per caller by
  `FILTER_LISTINGS_BY_PERMISSIONS`: capabilities describe the server, and making
  them per-caller would tell an under-privileged client the method does not
  exist rather than that it may not use it.

- **`ServerCapabilities.logging` removed.** It was never populated, and the
  `2026-07-28` revision deprecated the logging utility outright — leaving the
  field would only invite someone to fill it in.

- **`ScopeRequired([])` / `DjangoPermRequired([])` are refused.** `all(...)`
  over nothing is `True`, so an empty requirement permits everything while
  reading as a guard at the registration site — and satisfies the
  unguarded-tool check that would otherwise have warned.

- **`permissions=` now rejects entries that cannot gate**, on every
  registration method. Security-relevant rather than tidy:
  `permissions="ScopeRequired"` spreads into one entry per character; the tuple
  is non-empty so the unguarded-tool warning stays quiet, and at dispatch every
  entry is skipped and the call is **allowed** — a tool that reads as guarded
  and gates nothing. Only `has_permission` is required, so a custom permission
  that implements the gate and omits `required_scopes` remains valid.

### Fixed

- **A permission denial on `resources/read` or `prompts/get` lost its `403`
  when the client asked for progress.** A `progressToken` opened an SSE stream
  for *any* method, but the pre-flight that recovers the normative status could
  only speak for `tools/call` — so those two answered a denial inside a `200`
  with no `WWW-Authenticate`, and a client acting on status read the denial as
  success. Access was still denied; the status and the challenge were not.

  Streaming is now gated on the dispatch being able to report at all, which
  fixes the same bug's other half: a chain tool, `resources/read` and
  `prompts/get` thread no reporter, so they were being handed a stream that
  emitted keepalives and exactly one event. Nothing loses a capability — none
  of them ever sent a frame. Threading a reporter through the chain path is
  worth doing separately, and `can_report_progress` is where it gets switched
  back on.

- **A modern-only `PROTOCOL_VERSIONS` was an unhandled 500.**
  `["2026-07-28"]` is a supported configuration and the natural end state once
  legacy is dropped, but two call sites indexed the *legacy* version list for
  their default, and on such a server that list is empty: every `initialize`
  and every header-less `server/discover` raised `IndexError` out of the view.

  `server/discover` now answers on a modern-only server — refusing it would
  leave the server undiscoverable by exactly the clients it still serves — and
  `initialize` returns a JSON-RPC error naming the revisions that replaced the
  handshake. A header-less request mid-session is still rejected, since
  answering it with a modern version would tell a legacy client to speak a
  revision it cannot. An entirely empty `PROTOCOL_VERSIONS` is now refused at
  construction.

- **`content_kind=RESOURCE_LINK` rejected a tuple of mappings.** A selector
  returning one got an error explaining it had produced the wrong shape, which
  it had not.

- **`_StreamReporter`'s cap and monotonicity counters are guarded by a lock.**
  Frame delivery was already thread-safe; the two counters were plain
  read-modify-write on the same worker-thread path. Correct today only because
  `adispatch_spec` bridges to a single thread — a property of a collaborator,
  not of the class, and a service fanning reports across a pool could over-emit
  past `MAX_PROGRESS_NOTIFICATIONS` or slip a non-increasing frame through.

- **A permission implementing only `has_permission` hid a binding from
  listings but did not gate the call.** `is_binding_listable` duck-types;
  `check_permissions` skipped anything failing
  `isinstance(perm, MCPPermission)` — and because that Protocol is
  `runtime_checkable`, the check demands *every* member, including
  `required_scopes`, which the Protocol's own docstring documents as having an
  implied `[]` default. So a custom permission written to that documentation
  disappeared its tool from `tools/list` **and let the call through**. Dispatch
  now duck-types the same way listings always did, reading `required_scopes`
  defensively.

- **`ScopeRequired("mcp:admin")` silently became nine one-character scopes.**
  The constructor took a list and normalised with `list(scopes)`. Nothing
  failed at registration; it surfaced much later as a permission that could
  never be satisfied and a challenge reading `scope="m c p : a d m i n"`.

  It now accepts a bare string, exactly as `DjangoPermRequired` always has.
  That asymmetry *was* the bug — a developer who learned the permissive sibling
  naturally wrote the same thing here — so the fix removes the inconsistency
  rather than documenting it.

- **A legacy client was promised push notifications it could never receive.**
  `initialize` advertised `resources.subscribe` and all three `listChanged`
  flags whenever a subscription broker was configured — but every one of those
  notifications leaves through `subscriptions/listen`, which is a modern-only
  method. A legacy client acting on `subscribe` sent `resources/subscribe` and
  got `-32601`; one acting on a `listChanged` got something worse, because
  nothing answered at all and it simply waited. The same handshake also
  advertised `extensions`, which is not a field on the legacy
  `ServerCapabilities` and names a tasks extension a legacy client cannot
  declare per request and so can never reach.

  The advertised capabilities now follow **the caller's era**. `initialize` is a
  legacy method by definition, so it never offers either; `server/discover`,
  which both eras may call, answers according to the version the caller
  declared. Nothing changes for a modern client, and the registry-presence half
  is untouched — a legacy client is still told it has resources to read.

  **`resources/subscribe` is deliberately not implemented**, which is why the
  fix is the advertisement. It is optional in `2025-11-25` and gone from
  `2026-07-28`, where the schema says `SubscriptionFilter.resourceSubscriptions`
  *"replaces the former `resources/subscribe` RPC"* — and that is implemented.
  Building the legacy RPC would mean a cross-process session→URI registry
  serving only the era being carried for compatibility rather than grown.

## [0.23.0] — 2026-07-30

### Added

- **Outbound resource bounds — duration, result size and page size.**
  Consumer-reported: a `tools/call` over a list tool that expanded to 19 JOINs
  never returned. No status logged, no response written, the ASGI worker held
  until the server killed the instance ~71 s later; the client saw only "the
  connector's server isn't responding". `MAX_REQUEST_BYTES` has always bounded
  what a client can *send* — nothing bounded what a tool could produce or how
  long it could take.

  Four settings, all overridable per tool at registration, all accepting `None`
  to disable:

  - `MAX_RESULT_BYTES` (default 5 MiB, per-tool `max_result_bytes=`) — ceiling
    on one tool result or resource read, measured on the encoded wire payload.
    **Measured on the wire, not on the rendered text**, because a successful
    result carries the payload *twice* — `structuredContent` plus the spec's
    backwards-compatibility text mirror — so a ceiling counting one copy would
    be wrong by 2× against the client's context window.
  - `MAX_PAGE_SIZE` (default 500, per-tool `max_page_size=`) — ceiling on the
    model-supplied `limit` of a `paginate=True` selector. Now advertised as
    `maximum` on the generated `inputSchema` **and** clamped at dispatch: the
    schema tells a well-behaved model what to ask for, the clamp is what stops
    us trusting it. `limit` previously had a floor (`max(1, …)`) and no ceiling.
  - `DISPATCH_TIMEOUT` (default 60 s, per-tool `dispatch_timeout=`) — wall-clock
    ceiling on one dispatch. **ASGI only** (a sync WSGI view cannot bound its
    own dispatch) and it does **not** reclaim the worker: a thread parked in a
    database driver's socket read is not interruptible by asyncio cancellation.
    It buys a *terminal protocol event* instead of an open request that never
    resolves — pair it with a database statement timeout.
  - `REQUIRE_LIST_PAGINATION` (default `False`) — escalates the new
    `UnboundedListWarning` to `ImproperlyConfigured`. A `paginate=False` LIST
    selector serialises whatever its selector resolves to, and unlike a
    paginated tool it **cannot be clamped honestly**: the result carries no
    metadata that would tell the model rows were dropped.

  **Over a ceiling a call fails; it is never truncated.** A clipped list reads
  as complete to a model, which then reasons from it — so the response is an
  `isError` result naming the remedy ("narrow the filter, lower `limit`"), which
  is what the spec means by a tool execution error carrying actionable feedback.
  Resource reads have no `isError` envelope, so theirs is a JSON-RPC error
  carrying the same message.

  Bounds resolve per binding with `UNSET` (drf-services' sentinel, reused rather
  than re-invented) rather than `None`-as-default, because `None` is a
  *meaningful* value for all four — "no ceiling for this one tool" has to be
  expressible.

- **Request-level query params over MCP — `query_params=` on tool
  registrations.** Consumer-reported alongside the bounds above. A `QueryParam`
  is a model-supplied argument that lands in `request.query_params` rather than
  `view.kwargs` — the channel a serializer reads when it branches on the query
  string (django-restql field selection, a serializer keyed on `?expand=`).
  MCP requests carry no query string of their own, so a *declared* per-call
  channel is the only correct source for one:

  ```python
  from rest_framework_mcp import QueryParam

  server.register_selector_tool(
      name="invoices.list",
      spec=SelectorSpec(kind=SelectorKind.LIST, selector=list_invoices),
      query_params=(QueryParam("query", description="fieldset, e.g. {id,number}"),),
  )
  ```

  Available on `register_service_tool` / `register_selector_tool`, both
  decorator forms, and `ToolDefinition`. The value is advertised in the tool's
  `inputSchema`, **popped** from the arguments, and handed to
  `build_offline_context(query_params=…)` — so it never reaches the spec as an
  input and the unknown-argument policy never sees it. `QueryParam` is
  drf-services' type, re-exported here like `UrlKwarg`.

  A `QueryParam` is **never required** (a read-shaping param the spec runs fine
  without cannot be), and one name cannot be both a `QueryParam` and a
  `UrlKwarg` — that raises at registration, since a value is popped once and
  routes to one channel. A `filter_set` field is **not** a query param:
  filter fields already flow through as ordinary arguments, and declaring one
  here would pop it out and silently stop it filtering.

### Changed

- **The MCP endpoint's own query string no longer reaches serializers.**
  Behaviour change, and the reason the feature above matters. Every dispatch
  path wraps the real Django `POST` to the MCP endpoint, and nothing replaced
  the wrapped request's `GET` — so whatever query string a client appended to
  that URL (`POST /mcp/?fields=all`) appeared in `request.query_params` for
  **every call on that connection**. It was undeclared, client-controlled,
  identical for every call in the session, and invisible to the model; anything
  reading `request.query_params` on the dispatch path picked it up.

  All nine `build_offline_context` call sites now pass `query_params=`
  explicitly — the registered params for a tool that declares them, an empty
  mapping everywhere else — so what `request.query_params` holds is a property
  of the binding rather than of whichever URL the client dialled.

  **If you were relying on that passthrough**, declare a `QueryParam` for each
  value on the tools that need it. It then arrives per call, is advertised to
  the model, and is checked at registration. Resources and prompts get the
  closing with no registration knob: a resource URI *is* a locator, so per-call
  read-shaping belongs in its URI template, whose variables already route to
  `view.kwargs`.

- **`tools/list` now advertises `maximum` on a paginated selector's `limit`.**
  Additive to the schema; a client that ignored the field is unaffected.

### Notes

- **`notifications/cancelled` remains unimplemented, deliberately.** It was
  reported alongside the above as a conformance gap; it is not. `2025-11-25`
  makes ignoring a cancellation notification a **MAY** (explicitly including
  "the request cannot be cancelled"), and `2026-07-28` makes the notification
  **stdio-only** — on Streamable HTTP, closing the SSE response stream is itself
  the cancellation signal. Honouring it as specified would also need a
  cross-process registry, since the notification arrives as a separate HTTP
  request that may land on another worker. Disconnect detection is the mechanism
  that addresses the underlying problem, and it belongs with the response-stream
  work rather than here.

## [0.22.0] — 2026-07-30

### Fixed

- **A permission denial returned HTTP 200 instead of 403, violating the MCP
  authorization spec.** That spec's error table is normative — `401` for
  "authorization required or token invalid", **`403` for "invalid scopes or
  insufficient permissions"** — and a denial went out as a `200` with JSON-RPC
  `-32002` tucked in the body. A client following the spec has no way to
  distinguish "you lack a scope" from a successful call it should keep parsing.

  The denial now returns **403** with a `WWW-Authenticate` challenge carrying
  `error="insufficient_scope"` and `scope="…"`, per RFC 6750 §3.1 — so a client
  learns what to request on the next authorization round instead of retrying the
  same token. The JSON-RPC `-32002` body is unchanged, so anything reading
  `data.requiredScopes` keeps working. A non-scope denial (e.g.
  `DjangoPermRequired`) is also a 403, but advertises no `scope=`: RFC 6750
  defines none for that case, and naming a scope the client cannot obtain would
  send it round a pointless loop.

  Corroborating evidence this was always the intent: `MCPAuthBackend`'s
  `www_authenticate_challenge(scopes=…)` parameter has existed, and been
  implemented by both backends, since the auth layer landed — **and nothing ever
  called it with scopes.** The capability was advertised on the protocol and
  never wired up, the same shape as the discovery-vs-registration mismatches
  fixed in 0.19.0–0.21.0.

  **Behaviour change for clients** that treat a `200` as success without
  inspecting the JSON-RPC envelope: those calls now surface as HTTP errors,
  which is the point.

  Found by writing the end-to-end flow test below — the assertion disagreed with
  the documented behaviour, and checking the spec showed the docs were right and
  the code wrong.

### Tests

- **A full end-to-end OAuth flow, in one test**: `POST /oauth/register/` (RFC 7591,
  the public-PKCE shape Claude's connectors send) → unauthenticated `401` carrying
  the PRM pointer → the authorize passthrough with a real logged-in user → DOT's
  token endpoint on PKCE alone → `initialize` → `tools/list` → `tools/call` that
  actually writes a row.

  This closes the gap the last three releases came through. Every existing suite
  authenticates with `AllowAnyBackend`, so **no test drove the MCP transport with a
  real OAuth token** — which is why DCR issuing unusable credentials (0.19.0), DCR
  clients that could not be issued an ID token (0.20.0), and audience enforcement
  that rejected every token (0.21.0) were each individually invisible. The legs
  were only ever tested apart. The new suite runs on
  `DjangoOAuthToolkitBackend` **with a resource URL configured** — the exact
  combination that was broken.

  Three companion passes: the discovery walk a client performs before it has any
  credential (PRM → AS metadata → `registration_endpoint`, asserted as a chain
  because it is the chaining that breaks); scope denial on a narrower grant; and
  session lifecycle on a real bearer, proving the session and the credential are
  independent.

## [0.21.0] — 2026-07-30

### Fixed

- **`DjangoOAuthToolkitBackend` rejected every bearer token as soon as a resource
  URL was configured, and enforcement could not be turned off.** Audience
  enforcement was implied by `resource_url` alone, and it reads the token's bound
  resource via `getattr(token, "resource", None)` — but **DOT's `AccessToken` has
  no `resource` field and DOT implements no RFC 8707 resource indicators at all**,
  so that is always `None`. `audience_matches(None, "https://…")` is `False`, so
  `authenticate` returned `None` for every valid token. Confirmed against DOT
  3.3.0: the model's fields are `application, created, expires, id, id_token,
  refresh_token, scope, source_refresh_token, token, token_checksum, updated,
  user`.

  Worse, the condition was inescapable. `resource_url=""` counted as "not None",
  so it skipped the settings fallbacks *and* enforced against the empty string;
  and clearing the setting entirely made PRM advertise `resource: ""`, which RFC
  9728 marks REQUIRED. A deployment had to choose between authenticating anybody
  and publishing valid metadata — the MCP spec requires the latter, so the
  bundled OAuth backend was unusable in any real deployment.

  - **`ENFORCE_AUDIENCE` is now a separate setting, default `False`.**
    `resource_url` means "the identity this server publishes"; enforcement means
    "reject tokens that don't carry it". Coupling them was the bug.
  - **`audience_getter=`** says where the audience actually lives — a JWT claim,
    a gateway header, a related row. The default still reads `token.resource`,
    which is meaningful for a swapped
    `OAUTH2_PROVIDER["ACCESS_TOKEN_MODEL"]` carrying that field.
  - **Turning enforcement on without a usable audience source raises
    `ImproperlyConfigured` at construction** — which is startup, since
    `MCPServer.__init__` builds the backend — naming both ways out. A server that
    rejects everything is a configuration error; discovering it as a per-request
    401 is what made this take a live deployment to find.
  - `resource_url=""` now means unset at every layer, and unconfigured PRM carries
    a `_warning` explaining the empty `resource` instead of leaving a blank
    required field.

  **Not a security regression.** The previous default was on-but-unsatisfiable,
  so no deployment can have been relying on it working — it rejected valid and
  invalid tokens alike.

  **Why the suite missed it** — sharper than "no resource URL was configured in
  the tool-call suites. The backend's own audience tests drive `authenticate`
  through a `_FakeToken` **that has a `resource` attribute DOT's real model does
  not**. The fake was more capable than the thing it stood for, so the audience
  path looked thoroughly covered while being dead in production. The new
  `tests/auth/backends/test_dot_backend_audience_reality.py` uses a genuine
  `AccessToken` row, and pins `"resource" not in AccessToken` so the divergence
  cannot silently return; the fake-token tests now pass an explicit
  `audience_getter` and say why.

### Documentation

- **Removed `OAUTH2_PROVIDER["REQUIRE_RESOURCE"]` from the DOT recipe.** That
  setting does not exist in django-oauth-toolkit — `grep -rn REQUIRE_RESOURCE
  oauth2_provider/` finds nothing in 3.3.0 — and the surrounding prose claimed
  DOT "handles" forwarding the `resource` parameter to the token when it is set.
  It does not. An operator following that recipe configured a no-op and then hit
  the 401 above.
- The mcp-inspector troubleshooting table listed this exact symptom ("Token
  accepted but every call still 401") and blamed the authorization server for not
  binding `resource`. The row now names the real cause.

## [0.20.0] — 2026-07-29

### Fixed

- **A dynamically registered client could not be issued an ID token, so the token
  endpoint returned 500 whenever the advertised `openid` scope was requested.**
  DCR never set `Application.algorithm`, leaving DOT's `NO_ALGORITHM` default, so
  `Application.jwk_key` raised `ImproperlyConfigured("This application does not
  support signed tokens")` as soon as oauthlib routed the exchange through the
  OpenID grant — after the user had already logged in and consented.

  This is the same shape as 0.19.0's public-client bug: **discovery advertised a
  capability the registration endpoint could not provision.**
  `id_token_signing_alg_values_supported` was a hardcoded `["RS256"]`, justified
  in a comment on the grounds that the value was inert because "we don't actually
  mint ID tokens". That is false wherever DOT *is* the authorization server with
  `OIDC_ENABLED` — its token endpoint mints them.

  - `id_token_signed_response_alg` (RFC 7591 §2) is now modelled and resolved to
    DOT's `algorithm`, and echoed in the registration response.
  - Omitted, it takes RS256 when `OAUTH2_PROVIDER["OIDC_RSA_PRIVATE_KEY"]` is
    configured, and otherwise registers no algorithm — today's behaviour, kept
    for deployments not doing OIDC.
  - Requesting RS256 without a server key is a `400` naming the missing setting.
  - **HS256 is refused outright**, for a reason worth recording: it signs the ID
    token with `client_secret`, and this endpoint leaves `hash_client_secret` at
    its default, so the column holds a PBKDF2 digest rather than the secret the
    client was handed. Accepting it would mint tokens whose signature can never
    verify — quieter than the 500, and harder to diagnose.
  - `id_token_signing_alg_values_supported` is now derived from the same key, so
    it is empty on a server that cannot sign rather than promising RS256.
  - **Registering `scope: "openid"` on a server with no signing key is refused.**
    Found by sweeping for the same shape rather than reported: that server
    publishes `openid`, so the scope check passes it, no algorithm is registered,
    and the token endpoint 500s exactly as before. The algorithm resolution alone
    misses it because nothing was *requested* — the scope is what makes an ID
    token mandatory. Only a client that declares the scope at registration is
    caught; one that registers bare and asks for `openid` at authorize is not
    visible from this endpoint.

  Two further findings from that sweep are **recorded, not fixed**, because both
  are low-severity and fixing either carries more risk than the symptom:

  - `registration_endpoint` is advertised in AS metadata whenever an issuer is
    configured, including when DCR is disabled or was never mounted. Unlike the
    bugs above this fails *cleanly* — an immediate, well-formed
    `403 {"error": "invalid_request", "error_description": "DCR is disabled"}` —
    and gating it on `DCR_ENABLED` would stop advertising a working endpoint for
    anyone who passes `dcr_enabled=True` to the mount without also setting the
    global, which is precisely the flow Claude's connectors depend on.
  - `capabilities.resources` is advertised at `initialize` even with zero
    resources registered, where `prompts` is deliberately gated on having at
    least one. The asymmetry is real but the consequence is an empty
    `resources/list`, not a failure.

  Checked and found sound: `resolve_structured_output` already refuses to
  advertise an `outputSchema` while `structuredContent` is disabled (the MCP-side
  instance of this exact bug class, guarded with a clear error); the `ui=` link
  refuses at registration when a tool doesn't emit `structuredContent`;
  `bearer_methods_supported`, `code_challenge_methods_supported`,
  `subject_types_supported` and `response_modes_supported` are all backed by what
  DOT actually accepts.

  On the related report that this reaches the client as a bare 500 rather than an
  OAuth error: `/oauth/token/` is DOT's view, not this package's — `build_oauth_urlpatterns`
  deliberately does not mount it — so the RFC 6749 §5.2 channel isn't ours to use.
  What is ours is refusing the registration that creates the condition, which is
  where RFC 7591 §3.2.2's `invalid_client_metadata` applies and where the user
  hasn't yet spent a login and a consent. The general case of an
  `ImproperlyConfigured` escaping DOT's token endpoint remains open.

### Added

- **`UndescribedToolWarning` and `REQUIRE_TOOL_DESCRIPTIONS`** — registering a
  tool with no description now warns, and can be escalated to
  `ImproperlyConfigured`, exactly as `REQUIRE_TOOL_PERMISSIONS` already did for
  permissions.

  The asymmetry is the point: two properties are equally required for a tool to
  be usable by a model — something must gate the call, and something must say
  what the call does — and only the first was checked. An undescribed tool was
  served through `tools/list` with an empty description, indistinguishable from a
  documented one anywhere in the package, the transport, or the test surface. The
  consumer who reported this found theirs by dumping every registered tool and
  reading the output by hand.

  Deliberately **no docstring fallback** for spec registration. Defaulting to
  `inspect.getdoc(spec.service)` would silence the warning by shipping prose
  written for the next developer to a model choosing between tools. The decorator
  paths that already fall back to `fn.__doc__` are unchanged; the check reports
  whatever survived that.

### Documentation

- **"Documenting tools"** in [concepts](docs/concepts.md) — the three channels
  that already feed `inputSchema.properties.*.description`: serializer
  `help_text`, `UrlKwarg(description=…)`, and (the gap) drf-services' `Annotated`
  marker vocabulary on `Unpack[TypedDict]` extras keys, which carries
  `InputRequired` / `NotClientInput` but no description.

  Prompted by a report that there is "no supported way to attach meaning to an
  individual argument", which led to one argument being explained in prose across
  three tool descriptions. Two of the three channels have worked all along —
  `UrlKwarg.description` is emitted by `UrlKwarg.json_schema()` — so the gap was
  discoverability, not capability.

## [0.19.0] — 2026-07-29

### Fixed

- **Dynamic client registration issued credentials that could never
  authenticate.** Every client registered through `/oauth/register/` completed
  the authorize leg — login, consent, redirect back with a code — and then died
  at the token exchange with `401 {"error": "invalid_client"}`. Two independent
  defects, either one sufficient:

  1. `token_endpoint_auth_method` — the RFC 7591 §2 field that actually carries
     this — was not modelled, so it never reached the dataclass and every
     registration fell through to `client_type = confidential`. A spec-compliant
     public-PKCE registration was silently downgraded into a client DOT then
     demanded authentication from, having registered specifically to say it had
     no credentials to authenticate with.
  2. The `client_secret` in the registration response was the PBKDF2 digest, not
     the secret. `DOT`'s `ClientSecretField.pre_save` hashes the column during
     `Application.objects.create()`, so reading the attribute back afterwards
     yields `pbkdf2_sha256$…`. The plaintext was never emitted and was
     unrecoverable, leaving confidential clients holding a credential that could
     not verify against itself.

  For Claude's custom connectors this was terminal rather than inconvenient:
  that flow cannot be handed a pre-provisioned `client_id`, so DCR is the only
  way in, and the operator-facing message ("check your credentials and
  permissions") pointed at neither cause.

  Both are fixed, and the endpoint now speaks RFC 7591's vocabulary rather than
  only DOT's:

  - `token_endpoint_auth_method` (`client_secret_basic` / `client_secret_post` /
    `none`) and `grant_types` are accepted and drive `client_type` /
    `authorization_grant_type`. The DOT-spelled fields remain as an escape
    hatch; supplying both is fine when they agree and a `400` when they don't.
  - The secret is generated before the `Application` is written, so the response
    carries the plaintext — alongside `client_secret_expires_at`, which §3.2.1
    makes REQUIRED whenever a secret is issued.
  - Public clients get no `client_secret` at all, per §2.
  - The response now reports every value it resolved —
    `token_endpoint_auth_method`, `grant_types`, `response_types` — read back
    from the registered row rather than echoed off the request. §3.2.1 permits
    an authorization server to substitute metadata but obliges it to say what it
    settled on, and an undisclosed substitution is precisely what made this
    undiagnosable: the client goes on behaving as what it asked to be while the
    token endpoint enforces something else.
  - `response_types` is derived from the grant per §2.1 (`authorization_code` →
    `["code"]`, `implicit` → `["token"]`, `client_credentials` / `password` →
    `[]`); an explicit value that contradicts the grant is a `400`.
  - `scope` is validated against DOT's scopes backend — the same set
    `validate_scopes` uses at authorize time. Previously it was echoed
    unchecked, and since DOT stores no per-application scope, a client naming a
    scope the server doesn't offer was told it had registered successfully and
    found out one leg later. **Potentially breaking** for a deployment whose
    clients register scopes outside `OAUTH2_PROVIDER["SCOPES"]`: those
    registrations now fail with a per-field `invalid_client_metadata` naming the
    offending values, where they used to return `201` and fail at authorize.

  The registration response gained fields; nothing was removed or renamed. The
  gap that let this ship is closed too: the DCR tests asserted a secret was
  *present* (a hash is present) and stopped at the registration endpoint. There
  is now a test that drives DOT's own token endpoint with what DCR handed out,
  for both the public-PKCE and confidential-secret paths.

### Changed

- **Selector-tool and chain-step rendering now go through drf-services'
  `render_spec_output`** instead of local renderer + context-resolver copies. No
  behaviour change — the copies had been brought to parity in 0.18.0, and this
  removes the parity requirement rather than restating it. It is what the repo's
  own "rendering is not reproduced locally" rule always said, now true on every
  dispatch path.

  Worth recording *why*: those copies are exactly where 0.18.0's two
  consumer-reported crashes came from. They bound serializer-context providers
  positionally where the sister repo binds by name, and they never applied DRF's
  baseline context. A second implementation that must be kept equal to the first
  will drift again; deleting it is the only fix that holds.

  Internal only. `handlers.utils.resolve_output_context` (added in 0.18.0) is
  gone with them — `render_spec_output` does that layering itself.

## [0.18.0] — 2026-07-29

### Fixed

- **Serializer-context providers were called positionally, so any provider that
  didn't lead with `(view, request)` raised `TypeError`.** The sister repo
  invokes every provider through the keyword pool — each gets the subset of
  `view` / `request` / the resolved-data extra it declares *by name* — so
  `def get_context(request, **extras)` works over HTTP and through drf-pai. This
  transport forwarded `provider(view, request, **declared)` unconditionally and
  the same provider died with `takes 1 positional argument but 2 were given`,
  500-ing the tool call. Now bound by name, matching the docstring's own parity
  claim. Applies to `output_serializer_context` on selector and chain tools, and
  to a resource binding's `kwargs_provider`, which had the same divergence.
  **Behaviour change:** a provider whose first two parameters are named
  something other than `view` / `request` (e.g. `def ctx(v, r)`) used to work
  here and now raises — the sister repo has always required those names, and
  this is the parity that was claimed. Rename the parameters, or accept
  `**kwargs`.
- **No baseline serializer context off the HTTP path, so
  `self.context["request"]` raised `KeyError`.** Over HTTP DRF hands every
  serializer `get_serializer_context()` — `request` / `format` / `view` — and
  serializers read those keys unguarded (`request.user`, `build_absolute_uri`,
  an ownership check in a `SerializerMethodField`). MCP rendering passed only
  what a spec's `output_serializer_context` provider returned, and nothing at
  all when no provider was declared, so a serializer that renders fine behind a
  view 500-ed the tool call. Every serializer this transport builds now starts
  from that baseline (drf-services' `base_serializer_context`), with the spec's
  provider merged over it: selector tools (retrieve / list / paginated), chain
  steps, `resources/read`, and the read-path input validators. Service tools
  render through drf-services' `render_spec_output` and are fixed there.
- **`resources/read` on the async transport rendered on the event loop.** The
  handler bridged the *selector* off-loop but then called
  `build_resource_contents` inline — and rendering is the ORM work:
  `output_serializer(...).data` iterates the value, so a `LIST` resource whose
  selector returned a (lazy) queryset evaluated it right there and raised
  `SynchronousOnlyOperation`. The binding's `kwargs_provider` ran inline too, on
  a comment claiming providers are cheap; its documented headline use is a
  scoping tenant / role lookup, which is a query. Both now go through `acall`,
  matching what drf-services 0.29 does for the same callables in
  `adispatch_spec`. The sync transport was never affected.

- **A selector tool's context provider saw an empty `view.kwargs`.** Rendering
  built a second, kwargs-less `OfflineServiceView`, so a provider scoping by a
  registered `UrlKwarg` (`view.kwargs["project_pk"]`) got `None` at render time
  while the selector itself got the real value. One view is now built per call
  and threaded through dispatch and rendering, as on HTTP.

### Changed

- Requires `djangorestframework-services>=0.29.0` (for `base_serializer_context`).
- `handlers.utils.invoke_context_provider` is replaced by
  `handlers.utils.resolve_output_context`, which returns the whole context
  (baseline + provider) rather than just the provider's return. Internal helper;
  renamed because it no longer does what its name said.

## [0.17.1] — 2026-07-28

### Fixed

- **`server.async_urls` no longer 403s every `POST` and `DELETE` under
  `CsrfViewMiddleware`.** `AsyncStreamableHttpViewSet.as_view()` wraps DRF's
  sync view in an async callable, and Django's CSRF middleware reads
  `csrf_exempt` off the *resolved* callable — the wrapper, which wasn't
  carrying the flag DRF sets. An MCP client authenticating with a bearer token
  has no CSRF token to present, so the transport was unusable on any project
  with Django's default middleware; `GET` looked healthy because it's a safe
  method. The sync mount was never affected. Reported against 0.11.3, present
  since the async transport shipped. Consumers who worked around it by
  `csrf_exempt`-ing the mounted patterns can drop that wrapper.
- **The same wrapper now also carries `login_required = False`** (Django 5.1+),
  so `LoginRequiredMiddleware` no longer 302s MCP calls to the login page.
  Authentication belongs to the MCP auth backend, which answers with a JSON
  `401` and a `WWW-Authenticate` challenge. Every attribute DRF and Django set
  on the sync view is now copied wholesale rather than by allowlist, so a
  future flag can't go missing the same way.

## [0.17.0] — 2026-07-28

### Added

- **`UrlKwarg(required=True)` — advertise a route capture the spec can't run
  without.** The name joins the tool's `inputSchema` `required` list, so a model
  is told up front instead of discovering it through a failed call. `spec.partial`
  does not relax it: partial validation is about the payload the serializer
  checks, and a URL kwarg is never part of that payload.
- **A missing required URL kwarg is an `isError` validation tool result**, on
  every path — the sync and async JSON-RPC handlers, the selector tool, and the
  in-process `call_tool` / `acall_tool` surface. Schema `required` is only a hint
  and models omit required arguments routinely, so advertising it without
  enforcing it would have changed nothing. The message names the missing
  argument, so the caller can retry.

### Changed

- **`UrlKwarg` now comes from `djangorestframework-services`** (0.28), which owns
  the single definition. This package and `djangorestframework-pydantic-ai` each
  carried a copy, and the copies had **already drifted**: they validated the same
  declaration against different reserved-name sets, so `UrlKwarg("order")` was
  legal here and rejected there, and `UrlKwarg("user")` the reverse.
  `from rest_framework_mcp import UrlKwarg` keeps working — the import path is
  preserved permanently, so consumers need only a version bump.
- **`validate_url_kwargs` delegates to drf-services' `validate_channel_names`**,
  which owns the pool-seed half of the reserved set; only the pagination names
  (`ordering` / `page` / `limit`) are contributed here. It also now rejects
  `required=True` paired with a `default`.
- **Requires `djangorestframework-services>=0.28.1,<0.29`** (was `>=0.27,<0.28`).

### Fixed

- **`RESERVED_POOL_SEEDS` was a key behind the set it mirrored.** The local copy
  omitted `collection`, so a `UrlKwarg("collection")` — a name that *does*
  override a dispatcher-controlled pool seed — passed registration here while
  being rejected upstream. It is now re-exported from drf-services rather than
  duplicated. The stable `rest_framework_mcp.constants` import path is preserved.

## [0.16.0] — 2026-07-27

### Added

- **Tools link to interactive views.** `ui=UIToolMeta(resource_uri="ui://…")`
  on `register_service_tool` / `register_selector_tool` /
  `register_chain_tool` (and the `@service_tool` / `@selector_tool`
  decorators) emits `_meta.ui` on the tool's `tools/list` entry, so an MCP
  host renders that tool's result inside the view instead of showing raw JSON.
  This closes the MCP Apps round trip started by `register_ui_resource`.
  - **The render payload is the `structuredContent` the tool already emits** —
    no second serialisation path, pagination envelope included. A `tools/call`
    the view makes arrives at the ordinary endpoint, so `permission_classes`,
    per-binding `MCPPermission`s and rate limits all apply unchanged.
  - `visibility` (`UIVisibility.MODEL` / `APP`) declares who may call the tool.
    **Host-enforced** — a host must not offer the model a tool whose visibility
    omits `MODEL` — so this server declares the field and does not filter
    `tools/list` on it.
  - **Three ways a link can be wrong are refused at registration**, because all
    three fail identically at runtime: a view that silently never renders.
    (1) `resource_uri` names no view on this server — so a view must be
    registered *before* the tool linking to it; (2) the tool doesn't emit
    `structuredContent`, checked against the *effective* value so a project
    that disabled it globally is caught too; (3) both `ui=` and a `"ui"` key in
    `meta=`, which would silently overwrite each other.
- **`ClientCapabilities.extensions`** — a client's `initialize` now round-trips
  the protocol extensions it advertises (MCP Apps arrives as
  `io.modelcontextprotocol/ui`). Parsed for introspection only: **advertisement
  is one-directional, client → server**, the spec defines no matching server
  capability, and nothing gates on it — remembering it per session would mean a
  breaking change to the pluggable `SessionStore` Protocol, for metadata
  non-supporting clients are required to ignore anyway.

- **Interactive views — the server half of
  [MCP Apps](https://github.com/modelcontextprotocol/ext-apps).** A tool can
  declare an HTML view that an MCP *host* renders inline in the chat.
  `MCPServer.register_ui_resource(name=…, uri="ui://…", template_name=…)`
  serves the document with the `text/html;profile=mcp-app` mime type and a
  `_meta` bundle built from the typed `UIResourceMeta` — CSP origins (`UICsp`),
  browser `UIPermission`s, publisher `domain`, border preference.
  - **We declare; hosts render.** The sandboxed iframe, CSP *enforcement* and
    the `ui/*` postMessage bridge belong to the host and are deliberately not
    implemented here. Apps is an *extension* over base MCP `2025-11-25`, which
    this package already speaks, so there is **no protocol bump**, no transport
    change and no new capability to advertise.
  - **A view is an ordinary resource.** It shares one URI namespace with data
    resources (a collision raises as always), appears in `resources/list`, and
    honours `permissions=` / `always_listed`. Views are **unguarded by
    default**: the MCP session is already authenticated, a view is a static
    asset rather than tenant data, and hosts may prefetch one before any tool
    call.
  - Exactly one content source — `template_name=` (a Django template, rendered
    per read so an edit needs no restart), `html=`, or a zero-argument
    `selector=`. **The template renders with no context**: because hosts
    prefetch and cache views, a view is a shell that hydrates itself from tool
    results, and rendering a queryset into it would leak data across that
    cache.
  - `meta=` stays available for other extensions. Passing both the typed `ui=`
    and that same key inside `meta=` raises at registration rather than letting
    one silently win — the symptom would be a view that never renders.
- **`ResourceEncoding` — a resource declares how its body is encoded.**
  `register_resource(..., encoding=ResourceEncoding.TEXT)` returns the
  selector's value verbatim instead of JSON-encoding it. **This fixes a live
  bug for any non-JSON resource:** `mime_type=` has always accepted anything,
  but both read handlers JSON-encoded unconditionally, so a Markdown, CSV,
  plain-text or HTML resource came back as a *quoted JSON string literal*
  rather than the document. Declared rather than sniffed from `mime_type`, so
  advertising a new type never silently changes the body. A `TEXT` resource
  whose selector returns a non-`str` comes back as a JSON-RPC error on the read
  rather than raising through the transport.

- **Generic `_meta` passthrough on the wire types and every registration
  surface.** The base MCP protocol gives most wire objects a free-form `_meta`
  object — the open extension namespace, distinct from the closed
  `annotations` hint bundle — and the package had no way to populate it. Pass
  `meta=` to `register_service_tool` / `register_selector_tool` /
  `register_chain_tool` / `register_resource` / `register_prompt`, to the
  `@service_tool` / `@selector_tool` / `@resource` / `@prompt` decorators, or
  to a `ToolDefinition` / `SelectorDefaults` / `ServiceDefaults`, and the
  bundle lands on the binding (`binding.meta`) and is emitted verbatim under
  the `"_meta"` key of that binding's listing entry (`tools/list`,
  `resources/list`, `resources/templates/list`, `prompts/list`) and — for a
  resource — on the `contents` block `resources/read` returns, on both the sync
  and async transports. Omitted entirely when empty, so existing payloads are
  byte-identical. Keys are passed through untouched: nothing is validated,
  reserved, or rewritten. Contributions are combined by `merge_meta` (shallow,
  later wins), the single seam a future feature injects its own key through.
  On the `tools/call` result envelope `_meta` is per-call rather than
  per-binding, so it is a `build_tool_result(..., meta=…)` argument.

### Changed

- **`resources/read` renders and encodes through one shared builder.** The sync
  and async read handlers are full parallel implementations, not wrappers, so
  the serializer/encoding step now lives in
  `output.build_resource_contents.build_resource_contents` and the two
  transports cannot drift apart on it. No wire change.

## [0.15.0] — 2026-07-27

### Added

- **`MCPServer.register_specs(registry, *, overrides=None)`** — bulk-register
  every spec in a `SpecRegistry` (`djangorestframework-services` 0.27+) as a
  tool. A project that exposes the same specs over MCP *and* another transport
  (an agent toolset, HTTP views) otherwise writes the list once per transport,
  and the lists drift. The registry is the one declaration site; this is the
  MCP end of it. Entries are walked in registration order and dispatched by
  spec type — `ServiceSpec` through `register_service_tool`, `SelectorSpec`
  through `register_selector_tool`.
  - **A source for the server's `ToolRegistry`, not a replacement.** Every tool
    still lands as a normal binding and names still share the one MCP tool
    namespace, so a collision raises exactly as before.
  - **MCP knobs stay MCP-side** via `overrides`, a per-name mapping of the
    keyword arguments handed to that entry's registration method — the spec
    registry carries only what is invariant across transports. An `overrides`
    key naming a spec the registry doesn't hold raises `ValueError` (a typo is
    not a silent no-op), and a knob belonging to the other spec kind
    (`paginate` on a `ServiceSpec`) raises `TypeError` from that method.
  - **Filtered views feed multiple mounts** — `registry.by_tag("public")`
    returns a new registry, so two servers can be given different projections
    with no shared state.
  - **Permission guards are not bypassed.** Registration runs through the same
    per-tool methods, so an unguarded spec still raises `UnguardedToolWarning`
    (or `ImproperlyConfigured` under `REQUIRE_TOOL_PERMISSIONS`). A spec that
    can't declare `permission_classes` can be guarded at the binding through
    `overrides`.
  - Returns the bindings in registration order, mirroring the per-tool methods.

### Changed

- **`djangorestframework-services` floor raised to `>=0.27,<0.28`** (from
  `>=0.26,<0.27`) — `SpecRegistry` is imported at module level by
  `register_specs`.

### Documentation

- **New [Settings reference](https://artui.github.io/djangorestframework-mcp-server/reference/settings/)
  — every `REST_FRAMEWORK_MCP` key in one place.** Settings were previously
  documented ad hoc, wherever a key happened to be relevant (`auth.md`,
  `concepts.md`, `observability.md`), so coverage was accidental and three live
  keys had never been documented at all: `MAX_REQUEST_BYTES` (the 1 MiB request
  body limit), `DEFAULT_OUTPUT_FORMAT`, and `SIMPLEJWT_ACCESS_COOKIE`. The page
  is now the single home for all 17, each with its default, the reason behind
  it, and the per-server override — including the one combination the MCP spec
  forbids (`INCLUDE_OUTPUT_SCHEMA` without `INCLUDE_STRUCTURED_CONTENT`) and
  the three removed collaborator keys that now raise `ImproperlyConfigured`.
- **Chain-tool types gained reference entries.** `ChainStep`, `ChainContext`
  and `ChainToolBinding` had none — `ChainContext` was never even named in the
  docs, although the chain recipe has readers writing `inputs=lambda ctx: …`
  against it.
- **Field-level documentation on the binding types now reaches the published
  reference.** 23 fields across `ToolBinding`, `SelectorToolBinding`,
  `ChainToolBinding`, `ResourceBinding`, `PromptBinding` and `ToolDefinition`
  were explained by `#` comments, which `mkdocstrings` does not render — so the
  reference showed bare names with no explanation for
  `display_name` / `display_description`, `include_structured_content`,
  `include_output_schema`, `argument_binding`, `unknown_arguments`,
  `always_listed`, `url_kwargs`, `spec_kwargs_provides`, `input_serializer`
  and `ResourceBinding.kind`, even though the source explained every one. They
  are attribute docstrings now. Comments that head a *group* of fields
  (`# Both kinds:`, `# Selector-only:`) stay comments — they document the
  grouping, not a field. No behaviour change.
- **A snippet in the rate-limiting recipe is valid Python again.**
  `ServiceSpec(service=…, ...)` put a bare `...` after a keyword argument,
  which is a `SyntaxError` — so the snippet couldn't be copied, and the whole
  fence was invisible to doc-checking tooling.

## [0.14.0] — 2026-07-24

### Changed

- **Selector tool `inputSchema` now reflects the selector's own signature.**
  `build_selector_tool_input_schema` folds in drf-services'
  `spec_to_json_schema` (0.26) — the *same* reflection the Pydantic-AI
  `SpecToolset` consumes — so a selector's declared parameters and an
  `**extras: Unpack[TypedDict]` (each key one property, the TypedDict's required
  keys marked required, the `request` / `user` / `view` seeds skipped) are
  advertised over MCP. Previously only an explicit `input_serializer`,
  `filter_set`, or `UrlKwarg` surfaced a selector argument, so a scoping value a
  selector read from its `extras` (a nested route's `parent_pk`) was invisible
  to the model over MCP even though it already reached PAI tool schemas — the
  two transports now advertise the same shape. The reflected keys are also added
  to the selector-tool unknown-argument "known" set, so a closed selector
  (`input_serializer` under `REJECT`) accepts them instead of flagging them as
  unknown — no more advertised-but-rejected argument. Explicit sources still win
  on a name collision: an `input_serializer` field or a registered `UrlKwarg`
  overrides a reflected key of the same name. An explicit `UrlKwarg` is still
  required only for a value that never appears in the selector signature — one a
  scoping `spec.kwargs` provider reads off `view.kwargs`.

## [0.13.0] — 2026-07-24

### Added

- **`UrlKwarg` — expose a URL route capture as a tool argument.** The MCP
  counterpart of a nested route's URL captures (the `project_pk` of
  `/projects/{project_pk}/widgets/`). Register them on a service or selector tool
  — `register_service_tool(url_kwargs=…)` / `register_selector_tool(…)`, the
  `@service_tool` / `@selector_tool` decorators, or a declarative
  `ToolDefinition`. Each is advertised in the tool's `inputSchema`, popped from
  the arguments at dispatch, and seeded into `build_offline_context(kwargs=…)` /
  `OfflineServiceView.kwargs` — from where drf-services spreads it into the
  selector / target pools (authoritative over the spec params, below a
  `spec.kwargs` provider). Because it is popped, it never reaches the spec as an
  ordinary input, so the unknown-argument policy never flags it. Its headline use
  is a scoping `spec.kwargs` provider that reads `view.kwargs`: over MCP that
  mapping was always empty, so such a provider mis-scoped (returned its fallback)
  for every caller — a `UrlKwarg` is how the model-supplied route value reaches
  it. A name cannot collide with a reserved transport key (`ordering` / `page` /
  `limit` pagination knobs or the `request` / `user` / `data` / `instance` /
  `serializer` pool seeds) or be declared twice; colliding with an ordinary spec
  input is *allowed* and is the intended way to route a route-capture the spec
  also reads (the value flows through `view.kwargs`, drf-services spreads it
  authoritatively). Works on both the JSON-RPC transport (sync + async) and the
  in-process `MCPServer.call_tool` surface.

### Changed

- **Raise the `djangorestframework-services` floor from `>=0.24.1` to `>=0.26`**
  (ceiling `<0.26` → `<0.27`). `UrlKwarg` relies on drf-services 0.26 spreading
  the off-HTTP view's `kwargs` into the dispatch pools. Tested ceiling raised to
  0.26.x.

## [0.12.0] — 2026-07-17

Configuration is now **per-server**: everything that identifies or configures a
server is a constructor argument, and `REST_FRAMEWORK_MCP` is no longer read on
the request path at all. This makes running more than one MCP server in one
project actually work — previously two mounts would serve, but they could not
differ and silently shared state.

**Breaking**, with a small migration: three settings that named a class by
dotted path are removed (they raise if left in place, naming the replacement),
and settings now resolve when a server is built rather than per request. Every
other setting survives as a **default**, so a single-server project that
configures settings and passes nothing keeps working.

The security-relevant fix: RFC 8707 audience binding was defeated across two
servers, because one global `RESOURCE_URL` meant a token minted for one mount
passed the audience check at another.

See [Multiple servers in one project](https://artui.github.io/djangorestframework-mcp-server/auth/)
for the two-server recipe.

### Added

- **`MCPConfig` + `build_mcp_config()` — the scalar settings are now per-server.**
  Twelve settings were read from `REST_FRAMEWORK_MCP` **on every request**, deep
  in the handlers. Read there they could only ever be global, so no two servers
  in one project could differ on any of them. They are now resolved **once**, in
  `MCPServer.__init__`, into a frozen `MCPConfig` threaded to the transport and
  to every handler via `MCPCallContext.config`:

  ```python
  MCPServer(name="internal", config=build_mcp_config(page_size=500))
  ```

  `REST_FRAMEWORK_MCP` remains the **default source** for every one of them, so
  a single-server project that configures settings and passes no `config=` is
  unaffected. Covers `PROTOCOL_VERSIONS`, `REQUIRE_PROTOCOL_VERSION_HEADER`,
  `INCLUDE_STRUCTURED_CONTENT`, `INCLUDE_OUTPUT_SCHEMA`, `ALLOWED_ORIGINS`,
  `DEFAULT_OUTPUT_FORMAT`, `MAX_REQUEST_BYTES`, `PAGE_SIZE`,
  `INCLUDE_VALIDATION_VALUE`, `RECORD_SERVICE_EXCEPTIONS`,
  `FILTER_LISTINGS_BY_PERMISSIONS`, `REQUIRE_TOOL_PERMISSIONS`.

  Use `build_mcp_config(**overrides)` rather than `MCPConfig(...)` directly — it
  layers your overrides over the project's settings instead of discarding them.

- `MCPServer.config`, exposing the resolved snapshot.
- `build_oauth_urlpatterns(auth_user_adapter=, dcr_enabled=,
  dcr_initial_access_token=)` and `SimpleJWTCookieAdapter(cookie_name=)`. The
  DCR gates were read from settings **per request**; they now resolve when the
  patterns are built, so two mounts in one project can gate DCR differently.
  `DCR_ENABLED` / `DCR_INITIAL_ACCESS_TOKEN` / `SIMPLEJWT_ACCESS_COOKIE` remain
  as the defaults. The `DynamicClientRegistrationViewSet` gates default to
  **closed**, so a hand-wired view that forgets them refuses registrations
  rather than opening them.
- **`MCPServer(title=...)`** — the spec's `Implementation.title`, which this
  package did not implement. The MCP spec splits the two deliberately: `name` is
  *"intended for programmatic or logical use"* (the stable identifier), `title`
  is *"intended for UI and end-user contexts"* (human-readable, optional, with
  clients falling back to `name`). Omitted from the wire when unset.
- `DjangoOAuthToolkitBackend(resource_url=..., authorization_servers=...,
  scopes_supported=..., resource_documentation=..., resource_metadata_url=...)`
  — all previously read from settings at request time, all now resolved once at
  construction, so two backends in one process can genuinely differ.
- `MCPServer(version=...)`, to go with `name=` — the wire version was previously
  only settable through `SERVER_INFO`.
- `MCPServer(description=...)` is now surfaced as the `initialize` response's
  `instructions` field (the MCP spec's slot for a server describing itself to a
  client). Omitted entirely when no description is given.
- `MCPCallContext.server_info` / `.instructions`, carrying the owning server's
  identity to the handlers.

### Changed

- **`REST_FRAMEWORK_MCP` is no longer read on the request path.** Every scalar is
  resolved when a server is constructed. Two consequences worth knowing:

  - **Mutating settings no longer reconfigures an already-built server.** If your
    tests wrap a request in `override_settings(REST_FRAMEWORK_MCP=...)` against a
    server built at URL-conf import, the change is now ignored — build the server
    inside the test with `config=build_mcp_config(...)` and mount that instead.
    (`AUTH_BACKEND` / `SESSION_STORE` already behaved this way, since they were
    resolved in `__init__`.)
  - **`DEFAULT_OUTPUT_FORMAT` now does something.** It was declared in the
    settings defaults and read by nothing — a tool registered without an explicit
    `output_format` always got JSON. It is now the real fallback. If you set it to
    `"toon"` expecting it to work, it will now take effect.

- Internal signatures gained the values they used to read from settings:
  `paginate(page_size=)`, `is_origin_allowed(origin, allowed_origins)`,
  `resolve_protocol_version(header, supported)`, `negotiate_protocol_version(...,
  config=)`, `resolve_structured_output(default_output_schema=,
  default_structured_content=)`, `validation_error_data(..., include_value=)`,
  `check_tool_permissions_declared(..., require=)`, `call_spec_tool(..., config=)`.
  The transport viewsets take `config=` alongside their other collaborators.
  Only affects code calling these directly.

- `SERVER_INFO` is now the **default source** for `name` / `version` rather than
  an override of them, and it is read **once, when the server is constructed**,
  instead of on every `initialize`. A project that configures `SERVER_INFO` and
  never passes `name=` keeps its current wire identity; a project that passes
  `name=` now gets what it asked for.
- `MCPServer(name=...)` defaults to `None` (meaning "take it from `SERVER_INFO`")
  rather than to the literal `"djangorestframework-mcp-server"`. Reading
  `server.name` still returns a resolved string. Only affects code that
  introspected `.name` on a server constructed without one — a value that was
  inert on the wire regardless.

### Removed

- **`REST_FRAMEWORK_MCP["AUTH_USER_ADAPTER"]`** — the last dotted path in the
  package. Pass the adapter to the contrib mount instead:

  ```python
  # before — settings.py
  REST_FRAMEWORK_MCP = {"AUTH_USER_ADAPTER": "myproject.oauth.MyAdapter"}

  # after — urls.py
  (
      *build_oauth_urlpatterns(
          server=server,
          include_authorize=True,
          auth_user_adapter=MyAdapter(),
      ),
  )
  ```

  This also **lifts a constraint**: adapters no longer have to be constructible
  with no arguments (a rule that existed only so a dotted path could
  instantiate them), so they can take real configuration —
  `SimpleJWTCookieAdapter(cookie_name="my-jwt")`.

  There is now **no `import_string` anywhere in the package**.

- **`REST_FRAMEWORK_MCP["AUTH_BACKEND"]` and `["SESSION_STORE"]`.** Both named a
  collaborator by dotted path — an indirection that only existed because
  `settings.py` cannot hold a live object. `urls.py` can, so pass the object:

  ```python
  # before — settings.py
  REST_FRAMEWORK_MCP = {
      "AUTH_BACKEND": "myproject.mcp.MyBackend",
      "SESSION_STORE": "myproject.mcp.MyStore",
  }

  # after — urls.py, where the server is built
  server = MCPServer(
      name="my-app",
      auth_backend=MyBackend(),
      session_store=MyStore(),
  )
  ```

  Omitting either still gives you the package default (`DjangoOAuthToolkitBackend`
  / `DjangoCacheSessionStore`) — only the *dotted-path* form is gone. Leaving
  either key in your settings raises `ImproperlyConfigured` naming the
  replacement, rather than being silently ignored: a dropped `AUTH_BACKEND`
  would mean a project that believes it configured authentication has not.

### Fixed

- **Cache-backed sessions are no longer shared between servers.**
  `DjangoCacheSessionStore` keyed every entry under a flat `drf-mcp:session:`
  prefix, so two servers mounted in one project shared one session namespace
  over the same Django cache: a session minted at `/public/mcp` satisfied
  `/internal/mcp`'s ownership check, and a `DELETE` against either destroyed the
  other's session. Stores built by `MCPServer` now key under the server's
  `url_namespace`. Not an authentication bypass — each server still validated
  every request's bearer token through its own backend.

  **On upgrade, existing cache-backed sessions stop resolving once** (the key
  prefix changes) and clients transparently re-`initialize` — the same path
  already taken for sessions written by pre-0.7 versions.

  A store you construct yourself is yours to namespace:
  `DjangoCacheSessionStore(namespace="internal")`.

  The namespace is the server's **`name`**, not its `url_namespace`: a server
  used only in-process (`acall_tool`, the `django-ag-ui` bridge) is never
  mounted, so its `url_namespace` is a meaningless default that would collide
  with a mounted server at the default namespace *even when their names differ*
  — and Django's duplicate-namespace check (`urls.W005`) cannot see a server
  that isn't in the URL conf. Keying on identity also means renaming a URL
  prefix is no longer a silent session purge. The namespace is hashed into the
  cache key, so a free-form `name` ("My Invoicing Server") still yields a key
  that is valid on backends like memcached.

- **`MCPServer(name=...)` now reaches the wire.** `name` and `description` were
  accepted by the constructor and stored, but nothing ever read them: the
  `initialize` response built its `serverInfo` from the global
  `REST_FRAMEWORK_MCP["SERVER_INFO"]` setting instead. Every server in a project
  therefore introduced itself with the same name, and a project mounting two
  servers had no way to tell them apart. The server instance is now the source
  of truth for its own identity.

### Security

- **RFC 8707 audience binding now works with more than one server.**
  `RESOURCE_URL` was a single global read at request time, so every server in a
  project claimed the same canonical resource and a token minted for
  `/public/mcp` passed the audience check at `/internal/mcp` — defeating the
  precise mechanism that exists to stop cross-resource token replay. The
  canonical URL is now per-server:

  ```python
  internal = MCPServer(name="internal-mcp", resource_url="https://example.com/internal/mcp/")
  public = MCPServer(name="public-mcp", resource_url="https://example.com/public/mcp/")
  ```

  `RESOURCE_URL` remains the default for a server that doesn't name its own, so
  single-server projects are unaffected. `resource_url=` configures the default
  auth backend; passing it *and* a custom `auth_backend=` raises, since a custom
  backend owns its own audience policy and the value would otherwise be dropped
  in silence.

- **A 401 challenge now points at the issuing server's own metadata.**
  `resource_metadata` in `WWW-Authenticate` came from the global
  `SERVER_INFO["resource_metadata_url"]`, so with two servers it could only ever
  be correct for one. It is now derived from each server's `resource_url`
  (the PRM endpoint mounts under the server's own prefix); an explicit
  `resource_metadata_url=` still overrides.

## [0.11.3] — 2026-07-16

### Changed

- Raise the `djangorestframework-services` ceiling from `<0.25` to `<0.26`
  (floor unchanged at `>=0.24.1`) so the MCP server installs alongside
  drf-services 0.25.x. 0.25.0 is additive for this layer; the one relevant
  change is the bugfix giving `collection_selector_spec` selectors the view's
  URL kwargs on the bulk path, which now flows through automatically.

### Added

- Docs recipe: [Expose a polymorphic action as tools](recipes/polymorphic-action.md)
  — expand a drf-services 0.25 `PolymorphicServiceSpec` into one flat tool per
  variant rather than advertising a `anyOf` union to the model.

## [0.11.2] — 2026-07-13

### Changed

- Raise the `djangorestframework-services` floor from `>=0.21.1` to `>=0.24.1`
  (kept `<0.25`) so the MCP server always dispatches through the two DRF-parity
  fixes shipped in drf-services 0.24.1: `SelectorSpec.filter_set` now validates
  and rejects an invalid filter value (a `400`/`ValidationError`, not a silent
  unfiltered result), and the `input_data` merge no longer corrupts
  form-encoded request bodies. MCP tool dispatch routes filter and input
  handling through drf-services (`dispatch_spec`), so both fixes reach MCP
  callers with no code change here. Refreshed the pinned dependency set to
  0.24.1 at the same time.

## [0.11.1] — 2026-07-08

### Changed

- Widen the `djangorestframework-services` dependency constraint from `<0.23`
  to `<0.25`, so the MCP server installs alongside drf-services 0.23.x and
  0.24.x. The previous `<0.23` cap excluded those releases, which meant the
  latest drf-services (selector input-schema fidelity, off-HTTP query params)
  could not be co-installed. Refreshed the pinned dependency set at the same
  time.

## [0.11.0] — 2026-07-08

### Changed (breaking)

- **`MCPServer.urls` / `.async_urls` now return a namespaced
  `(patterns, app_name, namespace)` triple** — the shape `path()` mounts directly,
  the `admin.site.urls` idiom, aligning with `django-ag-ui`'s `AGUIServer.urls`
  (the MOUNT cross-package symmetry). Mount **without** `include()`:

  ```python
  # before
  urlpatterns = [path("mcp/", include(server.urls))]
  # after
  urlpatterns = [path("mcp/", server.urls)]
  ```

  The endpoint URL **names are now namespaced** and unqualified within the
  namespace: `mcp-endpoint` → `mcp:endpoint`, `mcp-protected-resource-metadata` →
  `mcp:protected-resource-metadata` (default namespace `"mcp"`; override with the
  new `MCPServer(url_namespace="…")`). Update any `reverse()` / `{% url %}` calls
  and switch the `*server.urls` splat form to `path("mcp/", server.urls)`.

## [0.10.1] — 2026-07-03

### Fixed

- The `ImproperlyConfigured` raised for a mis-declared `argument_binding` now
  names the current enum member (`BUNDLE`) instead of the retired `DATA_ONLY`,
  so the message points at a member that actually exists. Internal comments and
  docstrings still using the old `DATA_ONLY` / `MERGE` / `REPLACE` vocabulary
  were updated to `BUNDLE` / `SPREAD_AUTHOR_WINS` / `SPREAD_CALLER_WINS` to match.

### Changed

- Widened the `djangorestframework-services` dependency to `>=0.21.1,<0.23` to
  allow the published 0.22.x line.
- Documentation: corrected the stale error-mapping and dispatch descriptions,
  documented the `[jwt]` extra and the in-process transport surface / tool
  annotations in the README, and completed the reserved-seed list in
  `docs/concepts.md`.

## [0.10.0] — 2026-07-02

### Added

- **`MCPServer.alist_tools` — async tool listing.** Listing itself is pure
  Python, but the per-caller listing-permission filter
  (`FILTER_LISTINGS_BY_PERMISSIONS`) can run a DB-backed check (e.g.
  `DjangoPermRequired`), which raises `SynchronousOnlyOperation` when the sync
  `list_tools` reaches it from inside an event loop — the exact context an async
  in-process consumer runs in. `alist_tools` runs the whole handler in a thread.
- **Scopes on the in-process transport surface.** `list_tools`,
  `alist_tools`, and `acall_tool` now accept `scopes=`, populating the synthetic
  `TokenInfo`, so a `ScopeRequired`-gated tool is listable and callable
  in-process exactly as it is over the wire. Previously `_call_context` hardcoded
  an empty scope set, making scope-gated tools permanently invisible / uncallable
  off-HTTP.

### Changed

- **`djangorestframework-services` floor raised to `>=0.21.1,<0.22`.** Picks up
  the collection-safe `enforce_permissions`, the object-permission guard firing
  on selector dispatch, and the conformance dispatch fixes.
- **Object-level permissions are now enforced on selector RETRIEVE reads through
  the spec-core surface (object-level half).** With the guard now firing
  on selector dispatch (drf-services 0.21), `MCPServer.call_tool`'s
  `on_target_resolved=enforce_permissions` hook denies an object-level
  `has_object_permission` failure on the resolved row — not just class-level
  denials.
- **`tools/list` advertises `additionalProperties` honestly.** A schema
  is stamped `additionalProperties: false` only when the runtime actually rejects
  unknown keys — i.e. under `REJECT` *and* with an `input_serializer` to validate
  against. A serializer-less binding can't reject (the service path downgrades
  `REJECT`; the read/chain validator short-circuits), so its schema now stays
  open, matching the runtime instead of over-claiming a closed contract.
- **Service tools under `REJECT` now reject the post-fetch keys `ordering` /
  `page` / `limit` (contract change).** Routing service dispatch through
  `dispatch_spec` means those keys are treated as unknown arguments on a mutation
  tool (they are the selector pipeline's, not a service's), so a client sending
  them to a `REJECT` service tool now gets `-32602` rather than having them
  silently dropped. Selector tools still consume and strip them as before.
- **Off-HTTP request/view synthesis unified on `djangorestframework-services`.**
  The selector, chain, resource, and prompt handlers now build their
  synthetic DRF request/view via the sister repo's `build_offline_context` /
  `OfflineServiceView` — the same synthesizer the service path already used —
  instead of the local `build_internal_drf_request` + `MCPServiceView`, removing
  the package's second parallel off-HTTP request synthesizer.

### Fixed

- **`collection_selector_spec` service tools can now be registered.**
  Registration validated that every required callable parameter has a source but
  did not recognise `collection` (the bulk / list-mutation target a
  `collection_selector_spec` resolves), so a bulk-mutation service declaring
  `collection` raised `ImproperlyConfigured` at registration. `collection` is now
  a recognised source, matching the existing `instance` handling.
- **The tracked examples build against the shipped API.** Both
  `examples/invoicing` and `examples/job_status` passed the removed `filter_set=`
  kwarg to `register_selector_tool()`, built `SelectorSpec` without the required
  `kind=`, set `output_serializer=` on `ServiceSpec` (now on
  `output_selector_spec`), and imported prompt types from stale module paths — so
  they crashed on import. They are updated to the 0.8+ API, and a CI smoke test
  now imports and builds each example server so this can't regress.

### Removed

- **`MCPServiceView` (breaking).** The MCP-local off-HTTP view adapter is removed
  in favour of `djangorestframework-services`' `OfflineServiceView`, which is
  structurally identical (`request` / `action` / `kwargs`). `spec.kwargs` /
  `output_serializer_context` providers now receive an `OfflineServiceView`.
  Import it from `rest_framework_services` if you referenced the type directly.

## [0.9.1] — 2026-07-02

### Security

- **`MCPServer.call_tool` now enforces a spec's class-level `permission_classes`
  for selector tools.** The spec-core in-process surface relied solely on the
  `on_target_resolved=enforce_permissions` hook, which never fired on selector
  reads (and `dispatch_spec` never consults `permission_classes` itself) — so a
  selector spec whose `has_permission` denied (e.g. `permission_classes=[DenyAll]`)
  returned its payload through `call_tool` with `isError=False`. `call_spec_tool`
  now calls `enforce_permissions(spec, context)` **upfront and unconditionally**
  for both spec kinds before dispatching, raising `PermissionDenied` as intended;
  the hook still adds object-level checks. Only the in-process spec-core surface
  (`MCPServer.call_tool`) was affected — the wire paths (`tools/call`,
  `acall_tool`) fold spec permissions into `binding.permissions` at registration
  and were never impacted. Independent of the `djangorestframework-services` pin.

## [0.9.0] — 2026-06-23

### Added

- **Public in-process transport surface on `MCPServer`** — two methods that let
  an in-process consumer (a bridge, a Pydantic-AI toolset, a management command)
  run tools exactly as a remote MCP client would, without the HTTP / JSON-RPC hop
  and without reaching into handler internals:
  - `MCPServer.list_tools(cursor=None, *, user, request=None)` returns one
    `tools/list` page with the same merged `inputSchema` (serializer fields plus a
    selector tool's filter / ordering / pagination arguments and the
    `additionalProperties` policy), the same `FILTER_LISTINGS_BY_PERMISSIONS`
    per-caller filter, and the same opaque-cursor pagination as the wire.
  - `MCPServer.acall_tool(name, arguments=None, *, user, request=None)` (async)
    invokes a tool with the **full** transport applied — the transport-level MCP
    permissions and rate limits, the selector post-fetch pipeline (filter / order
    / paginate), a selector binding's MCP-only `input_serializer`, chain tools, and
    the output format — everything the spec-core `call_tool` deliberately omits.
    Returns the wire's `dict` payload (`content` / `structuredContent` / `isError`)
    or a `JsonRpcError` for a protocol fault.

  Both build the call context internally from `user` + `request` (synthesising a
  minimal request when `request` is `None`).
- **`JsonRpcError` and `JsonRpcErrorCode` re-exported from the package root**, so a
  consumer of `acall_tool` / `list_tools` can branch on protocol faults without a
  leaf-module import.

## [0.8.0] — 2026-06-23

### Added

- **Service & selector tool dispatch now routes through drf-services'
  `dispatch_spec`** (pins `djangorestframework-services` 0.20). The wire handlers
  (`handle_tools_call` / `handle_tools_call_async`) and the selector pipeline
  hand off the spec-execution core — instance resolution, `input_serializer`
  validation, the kwarg pool (per the binding's `argument_binding` /
  `unknown_arguments`), the service / selector run, queryset shaping +
  `filter_set`, and the output-selector re-fetch — to the neutral core, keeping
  only the transport shell (MCP permissions / rate limits, ordering, pagination,
  output format, `structuredContent`). Two capabilities come for free by routing
  through the path that already composes them:
  - **bulk mutations over MCP** — `spec.many` (list payload), a
    `collection_selector_spec` target, and list-shaped output;
  - **object-level permissions over MCP** — `spec.permission_classes` now run via
    the `on_target_resolved=enforce_permissions` hook, so a `has_object_permission`
    rule denies over MCP (previously only `has_permission` was checked).

  The reproduced dispatch code is deleted: the local kwarg-pool builder
  (`build_call_pool`), the input-validation/instance-resolution helpers, and the
  output-selector re-fetch.
- **`MCPServer.call_tool(name, arguments, *, user, request=None)`** — a blessed,
  transport-neutral way to invoke a spec-backed tool off the HTTP / JSON-RPC
  path, returning the same `ToolResult` the wire handlers build. It is built on
  the sister repo's `dispatch_spec` / `render_spec_output` / `enforce_permissions`,
  so the spec-execution core — instance resolution, input validation, the service
  / selector run, the output-selector re-fetch, queryset shaping incl.
  `filter_set`, and the retrieve nullability contract — is shared rather than
  re-implemented. An in-process consumer (a bridge, a Pydantic-AI toolset, a
  management command) calls this instead of reaching into handler internals. It
  honours the binding's `argument_binding` / `unknown_arguments` policies and the
  spec's `permission_classes` (object-level checks included, via the
  `on_target_resolved=enforce_permissions` hook); the read-shaped transport extras
  (pagination, ordering, a selector binding's MCP-only `input_serializer`) and the
  transport-level MCP permissions / rate limits stay with the wire handlers. Chain
  tools are unsupported (they orchestrate several specs).
- **Tools auto-advertise MCP `ToolAnnotations` hints.** Every tool now
  carries the standard hints derived from its mutation profile: selector
  tools → `readOnlyHint: true`; service tools → `readOnlyHint: false` +
  `destructiveHint: true`; chain tools are read-only only when every step
  is a selector. `destructiveHint` / `idempotentHint` are emitted only for
  non-read tools (per the spec). Hints passed at registration via
  `annotations=` override the derived defaults (e.g.
  `annotations={"destructiveHint": False, "idempotentHint": True}`). The
  bundle lands on `binding.annotations` and the `tools/list` payload, so a
  client can gate destructive tools without a hand-set flag.

### Changed

- **`ArgumentBinding` is re-exported from drf-services, with renamed members
  (breaking).** `dispatch_spec` owns the argument-binding policy, so MCP
  consumes its enum rather than a parallel copy. The members are renamed to the
  neutral-core names — `DATA_ONLY` → `BUNDLE`, `MERGE` → `SPREAD_AUTHOR_WINS`,
  `REPLACE` → `SPREAD_CALLER_WINS` (and `AUTO` is now available). Update
  `register_*_tool(argument_binding=...)` call sites accordingly. Defaults are
  unchanged in effect (service tools `BUNDLE`, selector tools
  `SPREAD_AUTHOR_WINS`); the import path (`rest_framework_mcp.constants`) and
  `UnknownArguments` (`REJECT` / `PASSTHROUGH` / `IGNORE`) are unchanged.
- **Selector tools adopt drf-services' selector contract (two visible
  changes).** Routing selectors through `dispatch_spec` means: (1) validated
  args **spread** to the selector's declared parameters (coerced via the
  binding's `input_serializer`) rather than arriving as one `data` bundle —
  selectors are reads; a selector should declare its params (`def list(*,
  project_id, ...)`), not `def list(*, data)`. (2) A selector configured with
  queryset shaping / `filter_set` must **return a `QuerySet`**; a non-queryset
  return now raises `ImproperlyConfigured` (a loud developer-facing config
  error) instead of silently skipping the shaping.
- **JSON-Schema generation now delegates to drf-services.** MCP's
  `inputSchema` / `outputSchema` generation — serializer / dataclass / FilterSet
  → JSON Schema, including drf-spectacular `@extend_schema_field` /
  `@extend_schema_serializer` overrides and the LIST pagination envelope — now
  routes through the sister repo's `serializer_to_json_schema` /
  `output_to_json_schema` / `filterset_to_json_schema` (drf-services 0.19)
  instead of MCP-local copies. The emitted schemas are unchanged; the duplicated
  converters (`schema/utils.py`, `schema/spectacular_overrides.py`, and the
  `schema/filterset_schema.py` introspection) are deleted. MCP keeps the
  wrappers that stamp its transport-specific concerns (`additionalProperties`
  per `unknown_arguments`, the `ordering` enum, and `page` / `limit`).
- **Retrieve selectors now shape + filter before `.first()`.** A
  `kind=RETRIEVE` selector tool now applies queryset shaping
  (`select_related` … `extend_queryset`) and `spec.filter_set` to its
  queryset before materializing the single instance — so a "stats from a
  filtered set" retrieve works over MCP, matching the sister repo's
  `dispatch_spec`. `filter_set` on a retrieve spec is no longer rejected at
  registration (ordering / pagination remain collection-only). Internally,
  the selector dispatcher now delegates shaping + filtering to
  drf-services' blessed `apply_queryset_shaping` leaf instead of
  re-implementing it (the local `_apply_filter_set` / `_apply_spec_shaping`
  helpers are gone); non-queryset selector returns still pass through
  unfiltered.
- **Selector filtering is declared on the spec.** A selector tool now
  reads its `FilterSet` from `SelectorSpec.filter_set`
  (`djangorestframework-services` 0.18+) rather than a separate
  `filter_set=` argument at registration. The one declaration drives both
  the HTTP transport and MCP, so a project states its filterable shape
  once instead of re-passing it per transport. `ordering_fields` /
  `paginate` stay on the registration call — they are MCP pipeline
  mechanics with no spec analogue. Schema generation and dispatch are
  unchanged; `binding.filter_set` now delegates to the spec.
- Dependency range bumped to `djangorestframework-services>=0.20,<0.21`.

### Removed

- **`filter_set=` is no longer accepted** by `register_selector_tool`,
  the `@selector_tool` decorator, `ToolDefinition.selector`, or
  `SelectorDefaults`. Declare it on the spec instead —
  `SelectorSpec(kind=…, selector=…, filter_set=MyFilterSet)`. Pre-1.0
  hard cut, no deprecation shim (the consumer set is tiny).

## [0.7.1] — 2026-06-10

### Changed

- **Adopted drf-services' stable dispatch surface** (the sister repo's
  0.17 release). All dispatch-leaf imports — `run_service`,
  `arun_service`, `is_async`, `resolve_callable_kwargs`, `run_selector`,
  `arun_selector`, `is_queryset`, `apply_queryset_shaping` — now come from
  the `rest_framework_services` package root (the documented, semver-stable
  surface) instead of private `_compat` modules and internal `utils` paths.
  No behaviour change.
- Dependency range bumped to `djangorestframework-services>=0.17,<0.18` —
  required, since 0.17 removed the private `_compat` package this package
  previously imported from.

## [0.7.0] — 2026-06-10

### Security

- **GET (SSE) and DELETE now authenticate.** Previously only the POST path
  called `backend.authenticate`; the SSE stream and session termination were
  reachable with no credential. Both viewsets now 401 (with the backend's
  `WWW-Authenticate` challenge) on unauthenticated GET and DELETE.
- **Authentication runs before the session lookup on POST.** The old order
  (session existence → 404, then auth → 401) let an unauthenticated caller
  probe whether a session id was live. An unauthenticated request now always
  sees 401 regardless of session validity.
- **Sessions are bound to a principal.** `SessionStore.create` takes a
  required keyword-only `principal_id` (derived from the authenticated
  token; anonymous principals share `"anonymous"`), and every subsequent
  POST / GET / DELETE asserts the presented session belongs to the caller.
  A wrong-principal presentation renders the same 404 as an unknown id —
  deliberately indistinguishable, so ownership cannot be probed. DELETE
  only destroys sessions the caller owns.

### Changed

- **Breaking — `SessionStore` Protocol.** Custom stores must add a
  keyword-only `principal_id: str` parameter to `create` and implement
  `owner(session_id) -> str | None` returning the stored principal.
  Sessions written by pre-0.7 `DjangoCacheSessionStore` (which cached
  `True`) report no owner; clients transparently re-initialize.
- **Breaking — business failures are now `isError` tool results, not
  JSON-RPC errors.** Per the MCP spec, *execution* failures should be
  results the model can read and self-correct from. `ServiceError` and
  `ServiceValidationError` raised from a tool's service/selector (and from
  chain steps, which add `failedStep`) now return `isError: true` results
  with a JSON `{"error": {"type", "message", "detail"?}}` payload in
  `content[0]` and no `structuredContent`. JSON-RPC errors remain for
  protocol faults: the input serializer rejecting the arguments *shape*
  stays `-32602`, unknown tool / auth / rate-limit codes are unchanged.
  Clients matching on `-32000` for business failures must read the result's
  `isError` flag instead.
- **Breaking-ish — RETRIEVE selector tools handle missing rows.** A
  RETRIEVE selector returning `None` (or raising `Model.DoesNotExist`) now
  yields a `not_found` `isError` result instead of serializing `None`
  (previously a confusing near-empty object or an unhandled 500). QuerySet
  returns are materialized via `.first()`, matching sister-repo HTTP
  dispatch. Opt into the nullable-resource contract with sister-repo 0.16's
  `SelectorSpec(allow_none=True)` — a missing row then renders a successful
  `null` result.
- **LIST `outputSchema` now matches the payload.** `tools/list` previously
  advertised the single-item object schema for LIST tools while the call
  returned a bare array (or the pagination envelope) — strict clients
  validating `structuredContent` against `outputSchema` rejected every
  result. The schema is now kind-aware: `{type: array, items: …}`
  unpaginated, the `{items, page, totalPages, hasNext}` envelope with
  `paginate=True`. (For a fully spec-compliant *object*-shaped
  `structuredContent` on LIST tools, enable `paginate=True`.)
- Relaxed the exact `djangorestframework-services==0.15.1` pin to
  `>=0.16,<0.17` — an exact pin in a library dependency forced unresolvable
  conflicts on any host tracking a different version.

### Added

- **Adopted drf-services 0.16 (spec self-containment) over MCP:**
  - `ServiceSpec.instance_selector_spec` — update-shaped tools resolve
    their target row from the spec: the nested RETRIEVE selector runs
    against `{request, user}` + the raw arguments (the MCP analogue of URL
    kwargs) + the nested spec's `kwargs` provider, with queryset shaping
    and `.first()` materialization. The resolved instance is threaded into
    input validation (DRF-style `serializer(instance, data=…, partial=…)`,
    so instance-dependent `validate()` works identically over MCP and
    HTTP) and seeded into the service kwarg pool as `instance`. A missing
    row is a clean `not_found` tool-level error, not a 500.
  - `ServiceSpec.partial` — partial validation without an HTTP method to
    derive it from (`partial=True` accepts omitted fields; the generated
    `inputSchema` drops its `required` list so schema-strict clients stay
    in sync). The identifier the instance selector consumes must be part
    of the tool's input — a serializer field or spread argument — exactly
    like the URL kwarg on HTTP.
  - Bound, validated input serializer seeded into the service kwarg pool
    under `serializer` (opt-in declare-to-receive) — `serializer.save()`
    performs a DRF-correct create/update for serializer-owned persistence.
    `instance` and `serializer` joined the reserved pool seeds (clients
    cannot poison them via arguments); registration-time signature
    validation knows both as conditional sources, and `DATA_ONLY` tools
    may consume the payload via `serializer` instead of `data`.
- **`UnguardedToolWarning` on permissionless registration.** DRF
  viewset-level / `REST_FRAMEWORK`-default permissions do **not** apply
  over MCP; a tool registered with neither `spec.permission_classes` nor
  per-binding `permissions=[...]` now warns at registration. Set
  `REST_FRAMEWORK_MCP["REQUIRE_TOOL_PERMISSIONS"] = True` to refuse such
  registrations with `ImproperlyConfigured`.

## [0.6.2] — 2026-06-04

### Changed

- Bumped the pinned `djangorestframework-services` dependency to **0.15.1**.
- The selector-tool dispatch path now discriminates queryset shapes with the
  sister package's centralized `is_queryset()` predicate
  (`rest_framework_services.selectors.utils`) instead of a local
  `_is_queryset_like` duck-typing helper that probed for
  `.filter` / `.order_by` / `.count`. This is a precise `isinstance` check
  against `QuerySet` / `Manager`, so a domain object that merely exposes those
  method names is no longer mistaken for a queryset and routed through the
  FilterSet / ordering / `.count()` shaping path. Plain lists and tuples still
  paginate in-memory via `len()` + slice exactly as before. No public API or
  behavioral change for the documented queryset / list selector shapes.

## [0.6.1] — 2026-06-03

### Added

- **`display_name` / `display_description` on tool definitions and bindings.**
  Optional consumer-only metadata, accepted by `register_service_tool` /
  `register_selector_tool` / `register_chain_tool`, by
  `ToolDefinition.service()` / `.selector()` (and forwarded through
  `register_tools`), and carried onto the resulting `ToolBinding` /
  `SelectorToolBinding` / `ChainToolBinding`. The MCP server **never** emits
  them on the wire (`tools/list` ignores them) — they exist so a downstream
  library can render a richer label / blurb than the protocol `title` /
  `description`. Both default to `None`.

## [0.6.0] — 2026-06-03

### Added

- **Chain tools (`MCPServer.register_chain_tool`).** Sequence several
  `ServiceSpec` / `SelectorSpec` steps behind a single MCP tool, where later
  steps read earlier outputs — `retrieve x → write y → write z` with `z`
  derived from both `x` and `y`. Each `ChainStep` binds its result to an
  alias readable via `ctx[alias]`; an optional `inputs(ctx)` callable maps
  the validated tool arguments (`ctx.args`) and prior outputs to that step's
  kwargs. `atomic=True` (default) runs the whole sequence in one
  `transaction.atomic()` — any step raising `ServiceError` /
  `ServiceValidationError` rolls back every prior write and the JSON-RPC
  error carries `failedStep`. The advertised `inputSchema` is the chain's
  explicit `input_serializer` or the first step's (the first-step fallback);
  the response is the `output_alias` step (default: last) or `{alias:
  rendered}` for every serializer-bearing step under `output_all=True`. Each
  step's `spec.permission_classes` are AND-combined with chain-level
  `permissions` and checked up front. Sync + async. New public exports:
  `ChainStep`, `ChainContext`.
- **Resolved-data output serializer context (sister-repo 0.15+).** Output
  context providers may now declare a keyword for the data about to be
  serialized — `result` (service tool), `instance` (selector RETRIEVE), or
  `page` (selector LIST) — and the value is forwarded through tool dispatch
  (sync + async). This lets a provider run a single batched query against
  the exact objects being rendered instead of re-fetching. `view` /
  `request` stay positional, so existing `(view, request)` providers are
  unaffected. For the LIST path the context is resolved *after* the page is
  materialized and the provider receives the same object the renderer
  iterates, so an id-keyed batched query reuses the queryset's result
  cache.

### Changed

- **Bumped `djangorestframework-services` to `==0.15.0`** (additive — the
  resolved-data output-context feature above).

### Fixed

- **`kind=LIST` pagination now handles non-QuerySet selector returns.** A
  paginated LIST selector that returned a plain `list` / `tuple` previously
  hit `list.count()` (which takes an argument) and failed with the opaque
  `count() takes exactly one argument (0 given)`. Pagination now
  discriminates QuerySet vs sequence with `_is_queryset_like`: QuerySets
  count via `.count()`, sequences paginate in-memory via `len()` + slice,
  and a non-sized/non-sliceable return (e.g. a generator) raises a clear
  "must return a QuerySet or a sized, sliceable sequence" error.

## [0.5.1] — 2026-05-31

### Changed

- **Bumped `djangorestframework-services` to `==0.14.0`.** 0.14.0 is a
  purely additive release — it promotes the `UNSET` sentinel's type to
  the public API as `UnsetType` (previously the private `_Unset`). No
  breaking changes; this package's public surface is unaffected and the
  full suite passes unchanged against the new pin.

## [0.5.0] — 2026-05-22

### Changed (breaking)

- **Bumped `djangorestframework-services` to `==0.13.0`.** 0.13.0
  ships its own `SelectorKind` enum (required on `SelectorSpec`) and
  collapses `ServiceSpec`'s flat output pipeline into a single nested
  `output_selector_spec: SelectorSpec | None`. Both changes are
  visible across this package's public surface.

- **Adopted upstream `SelectorKind`.** The local enum added earlier
  in this release cycle is gone — `rest_framework_mcp` now re-exports
  `rest_framework_services.types.selector_kind.SelectorKind`. The
  values (`LIST` / `RETRIEVE`) and semantics are unchanged.

- **The `kind` kwarg is gone from the imperative registration API.**
  `MCPServer.register_selector_tool(...)` and
  `MCPServer.register_resource(...)` now read the selector shape from
  `spec.kind` (which `djangorestframework-services >= 0.13` makes a
  required field on `SelectorSpec`). The same is true for the
  underlying adapters (`selector_spec_to_tool`, `selector_to_resource`)
  and for `ToolDefinition.selector` (the `selector_kind=` argument is
  removed). Decorator forms `@server.selector_tool(...)` and
  `@server.resource(...)` still accept `kind=` because they construct
  a `SelectorSpec` from the wrapped function — but the value is only
  consulted when `spec=` is omitted; an explicit spec wins.

- **`SelectorToolBinding.kind` is now a derived property** that reads
  through to `binding.spec.kind`. The dataclass no longer stores its
  own copy. Cross-knob validation (`filter_set` / `ordering_fields` /
  `paginate` rejected when `spec.kind == RETRIEVE`) still runs in
  `__post_init__`.

- **`ServiceSpec` output pipeline now lives on a nested
  `output_selector_spec`.** The `@server.service_tool` decorator
  builds the nested spec automatically when `output_serializer=` /
  `output_selector=` is passed; the dispatch handlers
  (`handle_tools_call`, `handle_tools_call_async`, `handle_tools_list`)
  read every output-side field through it.

- **Dropped the `OutputSelector` Protocol re-export.** Sister-repo
  0.13 removed the Protocol — the post-mutation re-fetch selector is
  structurally a `RetrieveSelector` nested under
  `ServiceSpec.output_selector_spec`. Replace any
  `from rest_framework_mcp import OutputSelector` with
  `RetrieveSelector` (or drop the import — the structural shape was
  rarely needed at type-check time).

  **Migration**:
  - Every `SelectorSpec(...)` call must now pass `kind=SelectorKind.LIST`
    or `kind=SelectorKind.RETRIEVE`. The mechanical translation is
    "this is a list-shaped selector → `LIST`; this returns a single
    instance → `RETRIEVE`."
  - Every `ServiceSpec(output_serializer=..., output_selector=..., ...)`
    becomes `ServiceSpec(output_selector_spec=SelectorSpec(
    kind=SelectorKind.RETRIEVE, selector=..., output_serializer=..., ...))`.
  - Drop `kind=` from `register_selector_tool` /
    `register_resource` / `ToolDefinition.selector` (drop
    `selector_kind=`) calls — the value now travels on the spec.

### Added

- **Registration-time check that `input_serializer` fields actually
  reach the callable.** Both `selector_spec_to_tool` and
  `service_spec_to_tool` now raise `ImproperlyConfigured` at
  registration when:
  - `argument_binding=DATA_ONLY` but the callable doesn't declare a
    `data` parameter (nor accept `**kwargs`) — the validated payload
    would be silently dropped on dispatch.
  - `argument_binding=MERGE` / `REPLACE` and the serializer declares
    a field name the callable doesn't accept (and the callable has no
    `**kwargs` catch-all, *and* no `data` bundle parameter).
  Reserved pool-seed names (`request` / `user` / `data`) and selector
  post-fetch keys (`ordering` / `page` / `limit`) are exempted because
  the dispatch pipeline strips them from the spread before invoking
  the callable. Previously, mismatches would surface at the first
  client call with no observable error — fields were silently dropped
  by `resolve_callable_kwargs`.

- **Registration-time check that every required callable parameter
  has a static source on the MCP transport.** The reverse direction
  of the above. When an `input_serializer` is declared (i.e. you're
  opting *in* to a static input contract), every required parameter
  on the dispatched callable must be reachable from one of:
  - an `input_serializer` field (in `MERGE` / `REPLACE` mode);
  - a reserved pool seed (`request` / `user` / `data`);
  - the new `spec_kwargs_provides=(...)` opt-in declaring that
    `spec.kwargs(view, request)` will supply the value.

  Parameters with defaults, `**kwargs` callables, and `data`-bundle
  callables are exempt. `input_serializer=None` is "trust mode" —
  client args spread verbatim and the static check is skipped (only
  pool seeds and the opt-in still apply).

  Rationale: a `SelectorSpec` can be reused across DRF API views and
  MCP transports, but `spec.kwargs(...)` is a runtime callable whose
  output depends on the view context (URL path params on the API
  side, URI template variables on MCP resources, neither on MCP
  tools). Trusting `spec.kwargs` to satisfy a required parameter on
  the MCP side is therefore *opt-in* — list the parameter names in
  `spec_kwargs_provides=` at registration to make that trust visible
  at the transport boundary.

  `spec_kwargs_provides: tuple[str, ...]` is now accepted by
  `register_selector_tool`, `register_service_tool`, the
  `@server.selector_tool` / `@server.service_tool` decorators,
  `selector_spec_to_tool`, `service_spec_to_tool`, and
  `ToolDefinition.selector` / `ToolDefinition.service`.

## [0.4.0] — 2026-05-20

### Changed

- **Split `outputSchema` and `structuredContent` controls.** The single
  `INCLUDE_STRUCTURED_CONTENT` setting (and per-binding
  `include_structured_content` override) used to gate both the
  `outputSchema` advertisement in `tools/list` and the `structuredContent`
  field in `tools/call`. They are now independent: a new
  `INCLUDE_OUTPUT_SCHEMA` setting (default `True`) and matching
  per-binding `include_output_schema` override control the schema
  announcement separately. The MCP spec invariant — advertising
  `outputSchema` requires emitting conforming `structuredContent` — is
  enforced explicitly: the spec-violating combination is rejected with
  `ImproperlyConfigured` at construction time (explicit per-binding
  conflicts) or at request time (setting-level conflicts).

  **Migration**: if you previously set
  `REST_FRAMEWORK_MCP["INCLUDE_STRUCTURED_CONTENT"] = False`, also set
  `INCLUDE_OUTPUT_SCHEMA = False` (or accept the new
  `ImproperlyConfigured` raised on requests against bindings with an
  output serializer). If you set `include_structured_content=False` on a
  binding, add `include_output_schema=False` to the same registration
  call.

## [0.3.0] — 2026-05-20

### Changed

- **Structural cleanup** — pure refactor, no behavior
  change. Public top-level `rest_framework_mcp` re-exports unchanged.
  - **`types/` sub-packages.** Every parent package that mixed type
    declarations with functionality now has a `types/` sibling that
    holds the dataclasses and Protocols. Affected packages:
    `registry/`, `protocol/`, `auth/`, `auth/permissions/`,
    `auth/rate_limits/`, `transport/`, `handlers/`, `server/`,
    `contrib/oauth/`, `contrib/oauth/adapters/`. Internal imports
    point at the new leaf paths; package `__init__.py` re-exports
    preserve the existing public surface.
  - **`dict[str, Any]` → dataclasses** for the OAuth/auth metadata
    payloads. New `ProtectedResourceMetadata`,
    `AuthorizationServerMetadata`, `OpenIDDiscoveryPayload`,
    `DynamicClientRegistrationRequest`,
    `DynamicClientRegistrationResponse` (under `auth/types/` and
    `contrib/oauth/types/`). `MCPAuthBackend` Protocol signatures
    updated; both shipped backends and the OAuth views serialize via
    `.to_dict()`.
  - **`DynamicClientRegistrationSerializer` → `DataclassSerializer`**
    over the new request dataclass. The DCR ViewSet consumes the
    typed instance via `serializer.save()`.
  - **`View` / `APIView` → `ViewSet`** for every package-owned HTTP
    endpoint. Files + classes renamed:
    `ProtectedResourceMetadataView → ProtectedResourceMetadataViewSet`,
    `AuthorizationServerMetadataView → AuthorizationServerMetadataViewSet`,
    `OpenIDDiscoveryView → OpenIDDiscoveryViewSet`,
    `DynamicClientRegistrationView → DynamicClientRegistrationViewSet`,
    `StreamableHttpView → StreamableHttpViewSet`,
    `AsyncStreamableHttpView → AsyncStreamableHttpViewSet`. Each
    mounts via `.as_view({method: action}, ...)`; canonical action
    maps `STREAMABLE_HTTP_ACTION_MAP` and
    `ASYNC_STREAMABLE_HTTP_ACTION_MAP` re-exported from the
    ViewSet modules for terse URL conf. The async transport
    additionally overrides `as_view`/`dispatch` so Django routes
    the coroutine-returning view correctly (DRF's ViewSet dispatch
    is still sync-only as of 3.17). `AuthorizePassthroughView`
    stays as a DOT `AuthorizationView` subclass — documented
    exception, since the parent class lives in DOT.


- Bumped the `djangorestframework-services` pin from `==0.11.0` to
  `==0.12.0`. The MCP layer now honors three sister-repo additions
  automatically — no migration steps for existing tools / resources, but
  spec authors can now lean on them instead of duplicating the same
  configuration at the MCP registration call:
  - **`spec.permission_classes`** (DRF `BasePermission` classes) on both
    `ServiceSpec` and `SelectorSpec` (and on the `SelectorSpec` used for
    resources) is wrapped via the new `DRFPermissionAdapter` and
    prepended to the per-binding `permissions` tuple. Spec-declared
    permissions run first; tool-level `MCPPermission` instances run
    after. Misconfigurations (instances instead of classes,
    non-`BasePermission` subclasses) raise `TypeError` at registration
    time. The same spec that backs an HTTP view now governs the MCP
    binding without restating the permission contract.
  - **Per-spec QuerySet shaping** (`select_related`, `prefetch_related`,
    `annotations`, `extend_queryset`) on `SelectorSpec` is applied to
    the queryset returned by the selector before the FilterSet / ordering
    / pagination pipeline. `extend_queryset` runs last so it always sees
    the fully statically-shaped queryset, matching sister-repo's
    ordering. Non-queryset returns (lists, scalars) pass through
    shaping unchanged.
  - **Per-spec serializer context** (`input_serializer_context` and
    `output_serializer_context` on `ServiceSpec`,
    `output_serializer_context` on `SelectorSpec`) is invoked with the
    synthesised view + DRF request and forwarded as `context=` into the
    serializer constructor — both sync and async dispatch paths.

### Added

- New `REST_FRAMEWORK_MCP["FILTER_LISTINGS_BY_PERMISSIONS"]` setting
  (default `False`). When enabled, `tools/list`, `resources/list`,
  `resources/templates/list`, and `prompts/list` drop bindings whose
  `permissions` deny the current caller before paginating, so
  `nextCursor` reflects the user-visible slice. Per-binding
  `always_listed=True` (`ToolBinding` / `SelectorToolBinding` /
  `ResourceBinding` / `PromptBinding`, plus the matching server
  registration entry points and `ToolDefinition`) opts a binding back
  into the listing as a discovery aid for admin-only operations where
  the caller should see the name but can't invoke it. Custom
  permissions can override list-time visibility by declaring an
  `is_listable(token)` method alongside `has_permission`; the default
  falls back to `has_permission(synthetic_request, token)` for
  binding-level permissions like `ScopeRequired` /
  `DRFPermissionAdapter`. Filter is point-in-time only —
  per-call-argument permissions evaluate against a data-less request at
  list time, so this is binding-level gating, not per-record gating.
- Conformance test suite (`tests/conformance/`) — drives every binding
  feature through the live Django URL conf + JSON-RPC transport so the
  wire shape is what an MCP client actually sees. Covers
  `ArgumentBinding.MERGE` spread + pool-seed protection, all three
  `UnknownArguments` policies, `register_tools` bulk-registration
  parity, `spec.permission_classes` denial through the transport,
  every PRM / AS / OIDC / DCR endpoint in the contrib mount including
  alias-renders-not-redirects, and the SimpleJWT cookie adapter
  hydrating `request.user` before DOT's `AuthorizationView` dispatches.
  Suite shares the existing `jsonrpc` / `initialized_session` fixtures
  but routes through `tests.conformance.urls` (mounted via
  per-module `pytestmark = pytest.mark.urls(...)`).
- New `AuthUserAdapter` Protocol (`rest_framework_mcp.contrib.oauth.adapters`)
  plus a reference `SimpleJWTCookieAdapter` implementation behind a new
  `[jwt]` extra. Adapters hydrate `request.user` before DOT's
  `AuthorizationView` dispatches — the typical "DRF backend with
  SimpleJWT cookies on the same host" deployment where DOT's view
  doesn't know about the JWT cookie and would otherwise treat the user
  as anonymous. Configure via `REST_FRAMEWORK_MCP["AUTH_USER_ADAPTER"]`
  (dotted path) plus `REST_FRAMEWORK_MCP["SIMPLEJWT_ACCESS_COOKIE"]`
  (cookie name, default `"access"`). Mount the passthrough by passing
  `include_authorize=True` to `build_oauth_urlpatterns(...)` — the
  resulting view is a thin DOT `AuthorizationView` subclass with the
  adapter hook bolted onto `dispatch`. Without an adapter configured
  it's functionally identical to DOT's view, so the flag is safe to
  enable in every deployment.
- New `rest_framework_mcp.contrib.oauth` namespace with
  `build_oauth_urlpatterns(server=, include_dcr=, include_aliases=,
  include_openid_discovery=)` plus the underlying views
  (`AuthorizationServerMetadataView`, `OpenIDDiscoveryView`,
  `DynamicClientRegistrationView`, `DynamicClientRegistrationSerializer`).
  Opt-in glue — the core `MCPServer.urls` mount stays minimal (PRM
  only). Mount the helper alongside your server URLs to expose the full
  endpoint matrix MCP / LLM-host clients probe (RFC 8414 + RFC 9728 +
  OIDC discovery + RFC 7591 DCR) plus the alias paths different
  clients use. Aliases render the canonical payload — they're not HTTP
  redirects. DCR is gated behind two new settings:
  `REST_FRAMEWORK_MCP["DCR_ENABLED"]` (default `False`) and
  `REST_FRAMEWORK_MCP["DCR_INITIAL_ACCESS_TOKEN"]` (default `None`).
- `MCPAuthBackend` Protocol gained an `authorization_server_metadata()`
  method. `DjangoOAuthToolkitBackend` implements it (RFC 8414 payload
  derived from `SERVER_INFO`). `AllowAnyBackend` raises
  `NotImplementedError` — the contrib mount surfaces that as `501 Not
  Implemented` on the AS endpoints so a dev-mode server doesn't have to
  silently serve a fake AS metadata payload.
- New `register_tools(server, definitions, *, selector_defaults, service_defaults)`
  bulk-registration entry point plus the supporting `ToolDefinition`,
  `SelectorDefaults`, `ServiceDefaults` dataclasses and `ToolKind`
  discriminator enum. Additive — the existing imperative and decorator
  registration surfaces are unchanged. `ToolDefinition.service(...)` /
  `ToolDefinition.selector(...)` are the typed entry points; passing a
  list of definitions plus per-kind defaults dataclasses collapses
  repetitive registration boilerplate without parallelising the
  dispatch engine (it loops over the existing per-tool methods, so
  every guarantee and bug fix applies automatically). Per-definition
  kwargs win over defaults on conflict; ``None`` is the "no override"
  sentinel across both layers. Returns the list of resulting bindings
  in input order so test harnesses can introspect.
- New `UnknownArguments` enum and matching `unknown_arguments=` kwarg
  on `register_service_tool`, `register_selector_tool`, and their
  decorator forms. Controls how MCP `arguments` keys outside the
  binding's declared field set are handled:
  - `UnknownArguments.REJECT` (default) — outer `inputSchema`
    advertises `"additionalProperties": false` and the validator
    rejects unknown keys with `-32602` (per-field
    `non_field_errors`). Selector tools' pipeline-reserved keys
    (`ordering` / `page` / `limit` and filter-set property names) are
    automatically considered "known" so the policy doesn't fight the
    post-fetch pipeline.
  - `UnknownArguments.PASSTHROUGH` — outer `inputSchema` advertises
    `"additionalProperties": true`; unknown keys survive validation
    and are merged onto the validated payload before binding. The
    serializer's coerced values for *declared* fields still win over
    the raw values. Only effective on plain `Serializer` outputs;
    bare-dataclass inputs receive `IGNORE`-equivalent behaviour
    (a frozen dataclass instance isn't a merge target).
  - `UnknownArguments.IGNORE` — outer `inputSchema` advertises
    `"additionalProperties": true`; unknown keys are silently dropped
    after validation (the DRF default). Forward-compat mode.
  Reserved transport-pool seeds (`request` / `user` / `data`) are
  never treated as "unknown" — they're handled by the dispatch pipeline
  and silently dropped from any client spread regardless of policy.
- New `ArgumentBinding` enum and matching `argument_binding=` kwarg on
  `register_service_tool`, `register_selector_tool`, and their
  decorator forms. Controls how MCP `arguments` flow into the kwarg
  pool of the dispatched callable:
  - `ArgumentBinding.DATA_ONLY` (default for service tools) — historical
    shape, `arguments` only enter the pool as `data=<validated>`.
  - `ArgumentBinding.MERGE` (default for selector tools) — every key
    from the validated arguments (or raw arguments when no validator)
    is added to the pool as a top-level kwarg, so selectors can declare
    individual parameters (`def list_drafts(*, project_id, page=1)`).
    `spec.kwargs(...)` overrides on conflict so author-declared
    invariants win over client-supplied values.
  - `ArgumentBinding.REPLACE` — like `MERGE`, but the spread wins on
    conflict so `spec.kwargs(...)` can supply client-overridable defaults.
  Reserved keys (`ordering` / `page` / `limit` from the selector-tool
  post-fetch pipeline; `request` / `user` / `data` from the pool seeds)
  are stripped from the spread in `MERGE`/`REPLACE` modes, so clients
  can't poison transport-controlled state. The default for selector
  tools flips from data-only to merge; selectors that were registered
  expecting `data=<arguments-dict>` continue to receive it (`data=` is
  still in the pool in every mode) but can now also be declared with
  individual parameters.
- New `DRFPermissionAdapter` class (`rest_framework_mcp.auth.permissions`)
  that bridges a DRF `BasePermission` class into the `MCPPermission`
  Protocol. Re-exported from the top-level `rest_framework_mcp`. Construct
  one directly if you need the same DRF permission gating without going
  through `spec.permission_classes` (e.g. for tool-level overrides).
- New `REQUIRE_PROTOCOL_VERSION_HEADER` setting (default `True`). Some MCP
  clients omit the `MCP-Protocol-Version` header entirely on non-`initialize`
  requests, which the spec-compliant default rejects with HTTP 400. Set this
  to `False` to accept those requests by falling back to the first entry of
  `PROTOCOL_VERSIONS`. A present-but-unsupported version is still rejected
  either way — silently downgrading would mask a real version mismatch.
- New `INCLUDE_STRUCTURED_CONTENT` setting (default `True`) plus a matching
  per-tool override `include_structured_content` on `register_service_tool`,
  `register_selector_tool`, and their decorator forms. Controls whether
  `tools/call` responses include the `structuredContent` field alongside
  the human-readable `content[0]` text. Set the global to `False` (or the
  per-tool override) to omit it for clients that echo both fields back to
  the LLM and burn context, or that choke on the field altogether. The text
  payload still carries the full data, so no information is lost; clients
  just have to re-parse instead of getting a pre-parsed dict. The
  per-binding override is tri-state — `None` (default) inherits the
  global, `True`/`False` force the behavior regardless of the setting.
  When `structuredContent` is omitted for a binding, its `outputSchema` is
  also dropped from `tools/list` — the MCP tools spec requires that a tool
  declaring `outputSchema` always return conforming `structuredContent`, so
  the two are kept in lockstep to avoid advertising a contract the server
  then refuses to honor.

## [0.2.8] — 2026-05-19

### Changed

- Bumped the `djangorestframework-services` pin from `==0.9.0` to
  `==0.11.0`. Upstream merged the lenient and strict service / selector
  Protocols into a single shape per kind: `StrictCreateService` /
  `StrictUpdateService` / `StrictDeleteService` / `StrictListSelector` /
  `StrictRetrieveSelector` / `StrictOutputSelector` (and the `NoKwargs`
  empty `TypedDict`) were removed. This package re-exported all six
  `Strict*` Protocols at the top level — they are dropped from
  `rest_framework_mcp.__all__` and the public surface. Strict-typed
  extras stay possible on user-defined services by annotating
  `**extras: Unpack[YourKw]` directly on the function (no longer via a
  Protocol type argument). Other 0.11.0 additions — `create_model` /
  `update_model` / `delete_model` (plus async variants), generic
  `ChangeResult[Model]` — are not reachable from the MCP transport, so
  no further code changes were needed. 0.10.0 (serializer-context
  propagation for service-backed views) is also irrelevant to this
  package's dispatch path.

  **Migration:** rename any
  `from rest_framework_mcp import StrictCreateService` (etc.) to the
  unified name (`CreateService` etc.) and drop the trailing `ExtraT`
  type argument from each parameterised call site. The `@implements(...)`
  decorator pattern keeps working unchanged once the names update.

- Adopted the shared release-parity CI flow from
  `djangorestframework-services`. `release.yml` is now triggered by every
  merge to `main` and short-circuits to a no-op unless
  `rest_framework_mcp/version.py` was bumped past the most recent
  `vX.Y.Z` tag; the previous tag-trigger pipeline is gone. Bumped every
  workflow action pin off the Node-20-deprecated set
  (`actions/checkout@v5`, `astral-sh/setup-uv@v7`,
  `actions/upload-artifact@v5`, `actions/download-artifact@v5`).
  `tests.yml` now emits `coverage.xml` + `htmlcov/` per matrix cell and
  a new `coverage-badge` job publishes `coverage.json` to `gh-pages` on
  every push to `main`; the README's coverage shield reads it live
  instead of the previous static `100%-brightgreen` placeholder. The
  release flow itself is centralised in `scripts/release-publish.sh`
  (byte-identical with the sister repo) and parameterised through
  `make release-publish-prepare` / `release-publish-finalize`. No
  runtime behaviour changes — pure CI / release-tooling parity.

### Fixed

- Doc / code drift surfaced by an end-to-end audit:
  - README's "What ships in v1" section was missing prompts,
    selector-tool FilterSet / ordering / pagination, per-binding rate
    limits, async + SSE (with `RedisSSEBroker` and `SSEReplayBuffer`),
    and OpenTelemetry spans. Rewrote in lockstep with `docs/index.md`
    and dropped the "v1" qualifier.
  - `docs/quickstart.md` claimed `AllowAnyBackend` was the default
    auth backend; the default is `DjangoOAuthToolkitBackend`. Updated
    the dev snippet to tell users to swap it in explicitly.
  - `docs/concepts.md` and `docs/async.md` carried stale roadmap
    statements that have since shipped
    (`async_urls` + GET-side SSE, `RedisSSEBroker`,
    `InMemorySSEReplayBuffer` / `RedisSSEReplayBuffer`). Rewrote to
    describe the shipped state and link the recipes.
  - `RedisSSEBroker` docstring still claimed `Last-Event-ID` resume
    was unimplemented; the `RedisSSEReplayBuffer` pairing is in tree.
  - `docs/recipes/custom-permission.md` showed a permission example
    reading `request.user.tenant_id` at registration time, where no
    request exists. Updated to use a configured `settings` value and
    explained that permission args are captured at registration time.

## [0.2.7] — 2026-05-03

### Fixed

- `encode_toon` called `toon.dumps(...)`, which `python-toon 0.1.1`
  renamed to `toon.encode(...)`. With the `[toon]` extra installed,
  TOON encoding raised `AttributeError` instead of producing output;
  switched the call site (and its test fakes) to `toon.encode`.
  Bumped the `[toon]` extra floor from `python-toon>=0.1` to
  `python-toon>=0.1.3` so old `0.1.0` installs (which still expose
  `dumps`) can't satisfy the extra and silently re-introduce the
  break.

### Changed

- Bumped the `djangorestframework-services` pin from `==0.8.1` to
  `==0.9.0`. Upstream's only "breaking" change is typing-only — the
  strict service / selector Protocols no longer hardcode `request` and
  `user` in their fixed signatures; both are still placed in the
  framework's kwargs pool and reach a service either through named
  parameters or via `**extras: Unpack[HttpExtras[YourUser]]`. The MCP
  layer already builds the kwargs pool with `request` and `user` and
  dispatches via `resolve_callable_kwargs`, which filters by the
  callable's declared signature — so user-defined services keep
  receiving them with no behaviour change either way. 0.9.0 also adds
  `HttpExtras[UserT]`, the HTTP-scope `call_service` /
  `call_selector` helpers, the `@selector_action` GET-side companion
  to `@service_action`, and a `specs/` scaffold in `startserviceapp`,
  none of which are reachable from the MCP transport. No code changes
  were needed in this package. See the upstream
  [0.9.0 changelog entry](https://github.com/Artui/djangorestframework-services/blob/main/CHANGELOG.md)
  for details.

## [0.2.6] — 2026-05-01

### Changed

- Bumped the `djangorestframework-services` pin from `==0.8.0` to
  `==0.8.1` to pick up the typing fix that widened the `ExtraT` bound
  on `ServiceSpec` and `SelectorSpec` from `dict[str, Any]` to
  `Mapping[str, object]`, so user-defined `TypedDict` kwargs
  type-check cleanly under `ty` and `mypy`. No code changes were
  needed in this package. See the upstream
  [0.8.1 changelog entry](https://github.com/Artui/djangorestframework-services/blob/main/CHANGELOG.md)
  for details.

## [0.2.5] — 2026-05-01

### Changed

- Bumped the `djangorestframework-services` pin from `==0.7.0` to
  `==0.8.0` to pick up `ServiceSpec.input_data` (with the symmetric
  three-tier resolver), the new `NoKwargs` / `NoInput` re-exports,
  the `requestBody`-on-`DELETE` fix in `ServiceAutoSchema`, and the
  reordered `(InputT, …)` generic parameters on the delete service
  Protocols. No code changes were needed in this package — we only
  re-export `DeleteService` / `StrictDeleteService` and don't
  parameterize them. See the upstream
  [0.8.0 changelog entry](https://github.com/Artui/djangorestframework-services/blob/main/CHANGELOG.md)
  for details.

## [0.2.4] — 2026-04-30

### Changed

- Bumped the `djangorestframework-services` floor from `==0.6.0` to
  `==0.7.0` to pick up the new `implements(Protocol[...])` decorator
  and the reordered `(input, extras, result)` generic parameters on
  the strict service / selector Protocols. No code changes were
  needed in this package — the MCP layer doesn't reach into those
  generics directly. See the upstream
  [0.7.0 changelog entry](https://github.com/Artui/djangorestframework-services/blob/main/CHANGELOG.md)
  for details.

## [0.2.3] — 2026-04-29

### Fixed

- `docs/index.md` was significantly out of sync with `README.md` —
  the install matrix was outdated (`[toon]` + `[oauth]`
  only, missing `[redis]` / `[otel]` / `[filter]` / `[spectacular]`
  and the `uv add` block), and "What ships in v1" predated prompts,
  SSE, rate limits, and OpenTelemetry entirely. Aligned the
  canonical-content sections (badges, install commands, feature list)
  while keeping the two pages' framing differences — README is a
  GitHub landing pitch with badges; `docs/index.md` is the docs-site
  essay with the same badges plus the "What this is / When to use it"
  structure.

No code changes.

## [0.2.2] — 2026-04-29

### Fixed

- `docs/index.md` carried a duplicate `!!! warning "Alpha"` admonition
  that the README banner-removal in 183b9c7 didn't catch, so the docs
  site still warned visitors about a 0.1 that shipped a week ago.
  Removed and re-released so the tag-triggered `gh-pages` deploy picks
  up the change.
- Release tooling: rolled this tag through `make release-bump` rather
  than hand-edits, after fixing the `pyproject.toml` `current_version`
  drift and back-filling the `CHANGELOG.md` compare-link footer that
  the previous two manual releases hadn't updated.

No code changes.

## [0.2.1] — 2026-04-29

### Fixed

- README and docs linked to a stale owner for the sister repo
  (404). It lives at `github.com/Artui/djangorestframework-services` —
  links corrected in `README.md`, `docs/index.md`, and both `ServiceSpec` /
  `SelectorSpec` source links in `docs/concepts.md`. Re-released so the
  PyPI project description picks up the fix.

## [0.2.0] — 2026-04-29

Selector tools — the read-shaped sibling of `register_service_tool`.
Pinned to `djangorestframework-services==0.6.0`.

### Breaking changes

- **`register_tool` → `register_service_tool`** and
  **`@server.tool` → `@server.service_tool`**. The unqualified name was
  ambiguous once `register_selector_tool` arrived — the rename gives
  the two registration surfaces parallel naming. Pre-1.0, no
  deprecation shim. Update call sites: rename method invocations and
  decorator references; the rest of the kwargs stay the same.

### Added

- **`register_selector_tool`** (and `@server.selector_tool` decorator)
  — read-shaped tool registration backed by a `SelectorSpec`. The
  selector returns a raw queryset; the tool layer owns the
  filter / order / paginate pipeline. Pipeline knobs are all opt-in:
  - `filter_set=<django_filters.FilterSet>` — generates JSON Schema
    properties from `FilterSet.base_filters` and applies the FilterSet
    to the queryset at dispatch time. Supports `CharFilter`,
    `BooleanFilter`, `NumberFilter`, `Date/DateTime/TimeFilter`,
    `UUIDFilter`, `ChoiceFilter` (enum), `MultipleChoiceFilter`,
    `BaseInFilter`, `BaseRangeFilter`, and `ModelChoiceFilter` —
    unrecognised filter classes degrade to `{}` so a custom subclass
    never breaks `tools/list`.
  - `ordering_fields=[...]` — generates an `ordering` enum (asc + desc
    variants) and applies `qs.order_by(...)` after filtering.
  - `paginate=True` — adds `page` / `limit` arguments and wraps the
    response with `{"items", "page", "totalPages", "hasNext"}`.
- **`[filter]` optional extra** (`django-filter>=23`). Required only
  when a binding declares `filter_set=`; importing the package without
  it still works.
- **`SelectorToolBinding`** — new binding dataclass; the shared
  `ToolRegistry` accepts both `ToolBinding` (service tools) and
  `SelectorToolBinding` (selector tools) and `tools/list` /
  `tools/call` route by binding type.
- **`build_selector_tool_input_schema`** + **`filterset_to_schema_properties`**
  — exposed under `rest_framework_mcp.schema` for projects that want to
  introspect the merged input schema outside of the registration flow.
- **Recipe**: [Selector tool with FilterSet](docs/recipes/selector-tool-with-filterset.md)
  walks a list-invoices example end-to-end (selector, FilterSet,
  ordering, pagination, generated `inputSchema`).

## [0.1.0] — initial alpha

First public release. Spec target: MCP **2025-11-25** (Streamable HTTP).
Pinned to `djangorestframework-services==0.6.0`.

### Server, registries, dispatch

- **`MCPServer`** — pluggable MCP server. Imperative `register_tool`,
  `register_resource`, `register_prompt` and decorator forms
  (`@server.tool`, `@server.resource`, `@server.prompt`). Owns its own
  registries, auth backend, session store, SSE broker, and replay buffer
  as instance state — no module-level singletons.
- **Units of registration**: `ServiceSpec` (tools) and `SelectorSpec`
  (resources). Both are reused verbatim from
  `djangorestframework-services`; the MCP package never imports from
  `rest_framework_services.viewsets` or `views.mutation`. Bare callables
  on `register_resource` are rejected with a clear `TypeError` —
  decorators auto-wrap for ergonomics.
- **Handlers** — `initialize`, `ping`, `tools/list`, `tools/call`,
  `resources/list`, `resources/read`, `resources/templates/list`,
  `prompts/list`, `prompts/get`. Sync + async siblings; `tools/call`,
  `resources/read`, and `prompts/get` route through
  `arun_service_sync_safe` / `arun_selector_sync_safe` so genuinely async
  callables stay native and sync ones are bridged via `sync_to_async`.
- **Pagination** for the four list endpoints with opaque cursor tokens;
  page size set by `REST_FRAMEWORK_MCP["PAGE_SIZE"]`.
- **Per-spec kwargs providers** (sister-repo 0.6+) — declare extra
  kwargs on the spec; the dispatch layer synthesises an
  `MCPServiceView` (action + URI vars) so providers shared with the HTTP
  transport keep working.

### Transport

- **`StreamableHttpView`** (sync) and **`AsyncStreamableHttpView`**
  (ASGI). POST single JSON-RPC, GET SSE for server-pushed events, DELETE
  to terminate. Mandatory header validation: `MCP-Protocol-Version`,
  `MCP-Session-Id`, `Origin` allowlist; body-size cap via
  `MAX_REQUEST_BYTES`.
- **`SessionStore` Protocol** with `InMemorySessionStore` and
  `DjangoCacheSessionStore` shipped.

### Server-pushed SSE

- **`SSEBroker` Protocol** with `InMemorySSEBroker` (single-process)
  and `RedisSSEBroker` (multi-worker, behind the `[redis]` extra).
- **`SSEReplayBuffer` Protocol** for `Last-Event-ID` resume — opt-in.
  `InMemorySSEReplayBuffer` (per-session bounded `deque`) and
  `RedisSSEReplayBuffer` (Redis Streams with `MAXLEN ~ N` and `XRANGE`)
  shipped. When wired in, `notify` records before publishing and the SSE
  GET drains buffered events past `Last-Event-ID` before entering live
  mode.

### Auth

- **`MCPAuthBackend` Protocol** with `DjangoOAuthToolkitBackend`
  (default when `oauth2_provider` is installed, via the `[oauth]` extra;
  lazy-imported) and `AllowAnyBackend` (dev only).
- **`MCPPermission` Protocol** with `ScopeRequired` and
  `DjangoPermRequired` shipped.
- **Rate limits** — `MCPRateLimit` Protocol; three implementations:
  `FixedWindowRateLimit` (atomic `cache.add`+`cache.incr`),
  `SlidingWindowRateLimit` (timestamp list + prune), and
  `TokenBucketRateLimit` (continuous refill, burst-friendly). All keyed
  per user with `REMOTE_ADDR` fallback; custom keys via callable.
- RFC 9728 Protected Resource Metadata at
  `/.well-known/oauth-protected-resource`.
- RFC 8707 audience enforcement via `RESOURCE_URL` setting.

### Output

- JSON (default) and **TOON** (token-oriented, via the `[toon]` extra)
  output formats, plus an `AUTO` heuristic that picks JSON for small
  payloads / TOON for tabular data. TOON falls back to JSON with a
  warning when the extra is missing — a tool call never breaks because
  of an absent optional dep.

### Schema introspection

- `build_input_schema` / `build_output_schema` produce JSON Schema for
  DRF `Serializer` subclasses, bare `@dataclass` types, `ListField` /
  `ListSerializer` (with and without children), `ChoiceField`, and the
  standard scalar fields. PEP 563 (`from __future__ import annotations`)
  dataclasses resolved via `typing.get_type_hints`.

### Observability

- OpenTelemetry spans around `tools/call`, `resources/read`,
  `prompts/get` (no-op when `opentelemetry-api` isn't installed; pulled
  in via the `[otel]` extra). Spans carry `mcp.binding.name`,
  `mcp.protocol.version`, `mcp.session.id`, plus `mcp.resource.uri` for
  reads.
- Opt-in `RECORD_SERVICE_EXCEPTIONS` setting that calls
  `span.record_exception` on `ServiceError` from a tool service before
  mapping to JSON-RPC `-32000`.
- Opt-in `INCLUDE_VALIDATION_VALUE` setting that echoes the offending
  arguments back as `data.value` on validation rejections (off by
  default to avoid leaking PII / secrets).

### Tooling

- Documentation site — `mkdocs-material` + `mkdocstrings`, deployed to
  GitHub Pages via the tag-triggered `release.yml`.
- CI — lint (ruff + ty), test matrix (Python 3.10–3.14 × Django
  4.2/5.2/6.0 with appropriate exclusions), strict docs build, and a
  smoke job that installs the package with **no** dev group and **no**
  optional extras to verify the import path stays clean.
- Release — tag-triggered `release.yml` re-runs the full test suite as
  a final gate, asserts the tag matches `__version__`, then publishes
  to PyPI via OIDC trusted publishing and deploys docs.

### Conventions enforced

- One exported class or function per file.
- No view-layer coupling — `ServiceSpec` / `SelectorSpec` are the units
  of registration.
- No module-level or class-level mutable state.
- 100% line + branch coverage enforced by pytest (**451 tests** at
  release).

[Unreleased]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.35.0...HEAD
[0.35.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.34.0...v0.35.0
[0.34.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.33.0...v0.34.0
[0.33.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.32.1...v0.33.0
[0.32.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.32.0...v0.32.1
[0.32.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.31.0...v0.32.0
[0.31.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.30.0...v0.31.0
[0.30.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.29.0...v0.30.0
[0.29.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.28.1...v0.29.0
[0.28.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.28.0...v0.28.1
[0.28.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.27.0...v0.28.0
[0.27.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.26.0...v0.27.0
[0.26.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.24.1...v0.25.0
[0.24.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.24.0...v0.24.1
[0.24.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.17.1...v0.18.0
[0.17.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.11.3...v0.12.0
[0.11.3]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.11.2...v0.11.3
[0.11.2]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.11.1...v0.11.2
[0.11.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.8...v0.3.0
[0.2.8]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Artui/djangorestframework-mcp-server/releases/tag/v0.1.0
