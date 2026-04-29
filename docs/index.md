# djangorestframework-mcp-server

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

## What ships in v1

- `tools/list`, `tools/call`, `resources/list`, `resources/templates/list`,
  `resources/read`.
- Pluggable auth: `DjangoOAuthToolkitBackend` (default when DOT is installed)
  and `AllowAnyBackend` (development only).
- Output formats: JSON (default) and TOON (token-oriented; optional extra with
  safe JSON fallback).
- Origin allowlist + protocol-version validation + session lifecycle per the
  2025-11-25 transport rules.

## Install

```bash
pip install djangorestframework-mcp-server                       # JSON only
pip install "djangorestframework-mcp-server[toon]"               # +TOON encoder
pip install "djangorestframework-mcp-server[oauth]"              # +django-oauth-toolkit backend
pip install "djangorestframework-mcp-server[toon,oauth]"         # all optional extras
```

Optional extras degrade gracefully:

- TOON falls back to JSON with a runtime warning if `python-toon` is not installed.
- The OAuth backend module imports cleanly without `oauth2_provider`; the
  `ImportError` only fires if you actually configure the backend and a request
  reaches `authenticate()`.

## Where to next

- [Quickstart](quickstart.md) — copy-pasteable end-to-end recipe.
- [Concepts](concepts.md) — tools vs resources, sessions, output formats, origin
  allowlist.
- [Authentication](auth.md) — backends, permissions, RFC 9728 PRM, RFC 8707
  audience binding.
- [Recipes](recipes/index.md) — opinionated cookbook entries.
- [Reference](reference/index.md) — autodocs for `MCPServer` and the protocol
  types.
