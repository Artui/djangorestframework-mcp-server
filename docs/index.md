# djangorestframework-mcp-server

[![CI](https://github.com/Artui/djangorestframework-mcp-server/workflows/tests/badge.svg)](https://github.com/Artui/djangorestframework-mcp-server/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/djangorestframework-mcp-server.svg)](https://pypi.org/project/djangorestframework-mcp-server/)
[![Python versions](https://img.shields.io/pypi/pyversions/djangorestframework-mcp-server.svg)](https://pypi.org/project/djangorestframework-mcp-server/)
[![Django versions](https://img.shields.io/pypi/djversions/djangorestframework-mcp-server.svg)](https://pypi.org/project/djangorestframework-mcp-server/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/Artui/djangorestframework-mcp-server/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/djangorestframework-mcp-server.svg)](https://github.com/Artui/djangorestframework-mcp-server/blob/main/LICENSE)

Expose [`djangorestframework-services`](https://github.com/Artui/djangorestframework-services)
services and selectors as a [Model Context Protocol](https://modelcontextprotocol.io)
(MCP) server, conforming to MCP **2025-11-25** (Streamable HTTP).

## What this is

`djangorestframework-services` lets a Django project expose **services** (mutations,
side-effecting callables) and **selectors** (read callables) as a thin layer above
DRF. Both are registered through `ServiceSpec` and dispatched via
`inspect.signature` so any callable that names the kwargs it wants (`request`,
`user`, `data`, `instance`, …) just works.

`djangorestframework-mcp-server` is an **alternate transport** over the same primitives.
It mounts a single `/mcp` endpoint that speaks Streamable HTTP per the MCP
spec, and registers your `ServiceSpec`s as MCP tools and resources. It does not
walk DRF viewsets or routers — the unit of registration is the `ServiceSpec`,
not a view.

## When to use it

Reach for this package when:

- Your project already uses `ServiceViewSet` (or plain `ServiceSpec` definitions)
  and you want to expose those callables to an MCP-aware client (Claude Desktop,
  Inspector, Cursor, etc.).
- You want a single, spec-compliant `/mcp` endpoint with pluggable auth
  (django-oauth-toolkit out of the box) and a small surface that you can audit.
- You don't want to rewrite your service layer in a parallel framework.

Skip it when you don't need the MCP wire format — call your services directly.

## What ships

- **Tools** — `tools/list`, `tools/call` for `register_service_tool`
  (mutations) and `register_selector_tool` (reads, with optional
  `FilterSet` + ordering + pagination).
- **Resources** — `resources/list`, `resources/templates/list`,
  `resources/read` against `SelectorSpec`-backed callables; RFC 6570
  templated URIs.
- **Prompts** — `prompts/list`, `prompts/get` against render callables
  returning strings, `PromptMessage`s, or async coroutines.
- **Pluggable auth** — `DjangoOAuthToolkitBackend` (default when
  `oauth2_provider` is installed) and `AllowAnyBackend` (dev only).
  Per-binding `MCPPermission` classes (`ScopeRequired`,
  `DjangoPermRequired`) plus your own.
- **RFC 8707 audience binding** when `RESOURCE_URL` is configured;
  **RFC 9728 PRM** served from the configured backend.
- **Per-binding rate limits** — `MCPRateLimit` Protocol with
  `FixedWindowRateLimit`, `SlidingWindowRateLimit`, and
  `TokenBucketRateLimit` implementations shipped.
- **Output formats** — JSON (default) and TOON (token-oriented;
  optional extra with safe JSON fallback).
- **Async POST/DELETE + GET-side SSE push** — sync `urls` for WSGI,
  `async_urls` for ASGI; `MCPServer.notify(session_id, payload)`
  pushes JSON-RPC frames on the session's SSE stream. Per-worker
  `InMemorySSEBroker` or cross-worker `RedisSSEBroker` (behind
  `[redis]`); `Last-Event-ID` resume via
  `InMemorySSEReplayBuffer` / `RedisSSEReplayBuffer`.
- **OpenTelemetry instrumentation** — `mcp.tools.call`,
  `mcp.resources.read`, `mcp.prompts.get` spans (no-op without the
  `[otel]` extra installed).
- **Origin allowlist + protocol-version validation + session
  lifecycle** per the 2025-11-25 transport rules.

## Install

```bash
pip install djangorestframework-mcp-server                              # JSON only
pip install "djangorestframework-mcp-server[toon]"                      # +TOON encoder
pip install "djangorestframework-mcp-server[oauth]"                     # +django-oauth-toolkit backend
pip install "djangorestframework-mcp-server[redis]"                     # +Redis SSE broker for multi-worker ASGI
pip install "djangorestframework-mcp-server[otel]"                      # +OpenTelemetry instrumentation
pip install "djangorestframework-mcp-server[filter]"                    # +django-filter for selector-tool FilterSets
pip install "djangorestframework-mcp-server[spectacular]"               # +drf-spectacular schema overrides
pip install "djangorestframework-mcp-server[toon,oauth,redis,otel,filter,spectacular]"  # everything
```

…or with `uv`:

```bash
uv add djangorestframework-mcp-server                                   # JSON only
uv add "djangorestframework-mcp-server[toon]"                           # +TOON encoder
uv add "djangorestframework-mcp-server[oauth]"                          # +django-oauth-toolkit backend
uv add "djangorestframework-mcp-server[redis]"                          # +Redis SSE broker for multi-worker ASGI
uv add "djangorestframework-mcp-server[otel]"                           # +OpenTelemetry instrumentation
uv add "djangorestframework-mcp-server[filter]"                         # +django-filter for selector-tool FilterSets
uv add "djangorestframework-mcp-server[spectacular]"                    # +drf-spectacular schema overrides
uv add "djangorestframework-mcp-server[toon,oauth,redis,otel,filter,spectacular]"  # everything
```

Optional extras degrade gracefully:

- TOON falls back to JSON with a runtime warning if `python-toon` is not installed.
- The OAuth backend module imports cleanly without `oauth2_provider`; the
  `ImportError` only fires if you actually configure the backend and a request
  reaches `authenticate()`.
- `RedisSSEBroker` / `RedisSSEReplayBuffer` raise a clear `ImportError` from
  `__init__` if `redis` isn't installed.
- The OTel tracing helper yields a no-op span when `opentelemetry-api` isn't
  importable, so handlers stay branch-free.
- `filter_set=` on a selector tool raises a clear `ImportError` only if you
  declare it without `django-filter` installed.

## Where to next

- [Quickstart](quickstart.md) — copy-pasteable end-to-end recipe.
- [Concepts](concepts.md) — tools vs resources, sessions, output formats, origin
  allowlist.
- [Authentication](auth.md) — backends, permissions, RFC 9728 PRM, RFC 8707
  audience binding.
- [Recipes](recipes/index.md) — opinionated cookbook entries.
- [Reference](reference/index.md) — autodocs for `MCPServer` and the protocol
  types.
