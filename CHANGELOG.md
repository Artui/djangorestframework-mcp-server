# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

- **Structural cleanup (Phase 10h)** — pure refactor, no behavior
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
- Conformance test suite (`tests/conformance/`) — drives every Phase 10
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
  - `docs/concepts.md` and `docs/async.md` carried stale "Phase 6 /
    Phase 7 / no-replay" roadmap statements that have since shipped
    (`async_urls` + GET-side SSE, `RedisSSEBroker`,
    `InMemorySSEReplayBuffer` / `RedisSSEReplayBuffer`). Rewrote to
    describe the shipped state and link the recipes.
  - `RedisSSEBroker` docstring still claimed `Last-Event-ID` resume
    was "Phase 7c"; the `RedisSSEReplayBuffer` pairing is in tree.
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
  the install matrix was stuck at the Phase 1 era (`[toon]` + `[oauth]`
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

- README and docs linked to `github.com/arturveres/djangorestframework-services`
  (404). The sister repo lives at `github.com/Artui/djangorestframework-services` —
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

[Unreleased]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.6.1...HEAD
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
