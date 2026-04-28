# Observability

The MCP server emits OpenTelemetry spans around every dispatch — `tools/call`,
`resources/read`, `prompts/get`. Spans are scoped to the dispatch portion
(after binding resolution) so cheap validation rejections don't generate
trace noise.

## Install

```bash
pip install "djangorestframework-mcp-server[otel]"
```

`opentelemetry-api` is the only declared dep. You bring your own SDK and
exporter — typically `opentelemetry-sdk` plus an exporter for whatever
backend you're using (OTLP, Jaeger, Tempo, …).

## Wire it up

OTel auto-discovers the global `TracerProvider` you configure in your app
startup. The MCP package doesn't install one — it just calls
`opentelemetry.trace.get_tracer("rest_framework_mcp")` at span time.

```python title="myproject/asgi.py"
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider(
    resource=Resource.create({"service.name": "my-mcp-server"})
)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
```

That's it — `MCPServer.urls` (or `async_urls`) starts emitting spans on the
next request.

## Span shape

| Span name | When |
| --- | --- |
| `mcp.tools.call` | Wraps tool dispatch (permission check, rate limit, validation, service invocation, output rendering). |
| `mcp.resources.read` | Wraps resource dispatch (permission check, rate limit, selector invocation, output serialization). |
| `mcp.prompts.get` | Wraps prompt dispatch (required-args check, permission check, rate limit, render normalisation). |

### Common attributes

Every dispatch span carries:

- `mcp.binding.name` — the registered tool / resource / prompt name.
- `mcp.protocol.version` — `"2025-11-25"` etc.
- `mcp.session.id` — when present (every call after `initialize`).

`resources/read` adds `mcp.resource.uri` (the URI the client sent, with
template variables resolved).

## What's *not* in a span

The MCP package keeps the surface minimal in v1:

- Validation failures **before** binding resolution (unknown tool, malformed
  params) don't open a span. They're cheap to detect and clutter trace
  pipelines if every parse error becomes a span. If you want them, log them
  via Django's standard logging — the JSON-RPC error envelope carries the
  same info.
- Exception recording is *not* automatic by default. Service exceptions get
  caught and mapped to JSON-RPC errors before exiting the span; the OTel
  SDK never sees them. If you want `ServiceError` raised from a tool service
  attached to the span, opt in:

    ```python
    REST_FRAMEWORK_MCP = {
        "RECORD_SERVICE_EXCEPTIONS": True,
    }
    ```

    The handler then calls `span.record_exception(exc)` before mapping the
    error. `ServiceValidationError` is deliberately *not* recorded — it
    represents client-side input failure and would clutter alerting
    pipelines. Resource and prompt errors stay un-recorded too; they're
    tool-call-specific.

## Without `[otel]` installed

The package's `_compat/tracing.span(...)` helper falls back to a no-op span
when `opentelemetry.trace` is not importable. Handlers can call it
unconditionally — there's no branch in dispatch code, no runtime cost, and
no import-time failure. The smoke job in CI runs the package without any
optional extras to confirm this stays true.

## Sampling

The MCP package doesn't sample. Whatever sampler you configure on the
`TracerProvider` applies to every span — typical setups use a parent-based
sampler so spans are recorded if their parent (an upstream HTTP request,
say) was sampled.
