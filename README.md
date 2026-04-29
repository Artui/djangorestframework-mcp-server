# djangorestframework-mcp-server

[![CI](https://github.com/Artui/djangorestframework-mcp-server/workflows/tests/badge.svg)](https://github.com/Artui/djangorestframework-mcp-server/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/djangorestframework-mcp-server.svg)](https://pypi.org/project/djangorestframework-mcp-server/)
[![Python versions](https://img.shields.io/pypi/pyversions/djangorestframework-mcp-server.svg)](https://pypi.org/project/djangorestframework-mcp-server/)
[![Django versions](https://img.shields.io/pypi/djversions/djangorestframework-mcp-server.svg)](https://pypi.org/project/djangorestframework-mcp-server/)
[![Docs](https://img.shields.io/badge/docs-artui.github.io-blue.svg)](https://artui.github.io/djangorestframework-mcp-server/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/Artui/djangorestframework-mcp-server/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/djangorestframework-mcp-server.svg)](LICENSE)

Expose [`djangorestframework-services`](https://github.com/Artui/djangorestframework-services)
services and selectors as a [Model Context Protocol](https://modelcontextprotocol.io)
(MCP) server, conforming to MCP **2025-11-25** (Streamable HTTP).

## Idea

Register `ServiceSpec` instances directly — no DRF router or viewset
involvement. The unit of registration is the `ServiceSpec`, not a view.

```python
from django.urls import include, path
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer

server = MCPServer(name="my-app")

server.register_service_tool(
    name="invoices.create",
    spec=ServiceSpec(
        service=create_invoice,
        input_serializer=InvoiceInputSerializer,
        output_serializer=InvoiceOutputSerializer,
    ),
)

server.register_resource(
    name="invoice",
    uri_template="invoices://{pk}",
    selector=SelectorSpec(
        selector=get_invoice,
        output_serializer=InvoiceOutputSerializer,
    ),
)

urlpatterns = [path("mcp/", include(server.urls))]
```

A decorator form is also supported (`@server.service_tool(...)` / `@server.resource(...)`).
See the [quickstart](docs/quickstart.md) for the full end-to-end recipe.

- **Services** (mutations) → MCP **tools**.
- **Selectors** (reads) → MCP **resources**.
- A single `/mcp` endpoint speaks Streamable HTTP. The
  `/.well-known/oauth-protected-resource` endpoint comes mounted alongside.

## What ships in v1

- `tools/list`, `tools/call`, `resources/list`, `resources/templates/list`,
  `resources/read`.
- Pluggable auth: `DjangoOAuthToolkitBackend` (default when DOT is installed)
  and `AllowAnyBackend` (dev only). Per-binding `MCPPermission` classes
  (`ScopeRequired`, `DjangoPermRequired`) plus your own.
- RFC 8707 audience binding when `RESOURCE_URL` is configured; RFC 9728 PRM
  served from the configured backend.
- Output formats: JSON (default) and TOON (token-oriented; optional extra
  with safe JSON fallback).
- Origin allowlist + protocol-version validation + session lifecycle per
  the 2025-11-25 transport rules.

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

Optional extras degrade gracefully: TOON falls back to JSON with a runtime
warning if `python-toon` is not installed, and the OAuth backend module
imports cleanly without `oauth2_provider` — the `ImportError` only fires when
you actually configure it.

## Try it

Install [mcp-inspector](https://github.com/modelcontextprotocol/inspector) and
point it at your dev server:

```bash
npx @modelcontextprotocol/inspector --url http://localhost:8000/mcp/
```

Inspector lists tools, fills in arguments from the generated JSON Schema, and
walks the OAuth auth flow against your configured Authorization Server.

## Documentation

- [Quickstart](docs/quickstart.md) — copy-pasteable end-to-end.
- [Concepts](docs/concepts.md) — tools vs resources, sessions, output formats.
- [Authentication](docs/auth.md) — backends, permissions, audience binding,
  bring-your-own AS recipe.
- [Recipes](docs/recipes/index.md) — focused cookbook entries.
- [Reference](docs/reference/index.md) — autodocs for every public symbol.

## License

MIT.
