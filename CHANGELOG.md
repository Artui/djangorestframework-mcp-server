# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.6...HEAD
[0.2.6]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Artui/djangorestframework-mcp-server/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Artui/djangorestframework-mcp-server/releases/tag/v0.1.0
