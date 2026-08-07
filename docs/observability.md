# Observability

!!! note "Two independent surfaces"

    **Logging** (below) is always on and needs no extra dependency — it is how
    you find out *why* a request was rejected. **Tracing** needs the `[otel]`
    extra and tells you where time went. Reach for logging first when something
    returns an error you cannot explain.

## Logging

Before 0.25.0 this package emitted **nothing** — no logger anywhere, in a
library that terminates a wire protocol, authenticates every request and
rejects requests for four distinct reasons. It now logs under the
`rest_framework_mcp` namespace, one logger per module, configured like any
other library:

```python title="settings.py"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "rest_framework_mcp": {"level": "INFO", "handlers": ["console"]},
    },
}
```

### What lands where

| Level | Events |
|---|---|
| `WARNING` | Session rejections, authentication failures, and every outbound bound that fires (result size, dispatch deadline, page clamp) |
| `INFO` | `initialize`, and the protocol era each request selected |
| `DEBUG` | Per-call dispatch timing and result size |

### The session line is the one that matters

A rejected session logs **which** of its causes fired, even though the HTTP
response deliberately does not:

```
WARNING Session rejected: session-unknown (session=a1b2c3d4…, principal=42, method=tools/call) -> HTTP 404
```

The wire merges "unknown id" with "id owned by another principal" so a caller
cannot probe session ids. An operator reading logs is not the adversary that
protects against, and a log line is not the wire — so server-side you get the
exact condition.

### What is never logged

Bearer tokens, tool arguments, and tool results — the last two are your domain
data and may be anything at all. Session ids appear as a short prefix
(`a1b2c3d4…`), enough to follow one client across requests and not enough to
replay.

## Tracing

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

provider = TracerProvider(resource=Resource.create({"service.name": "my-mcp-server"}))
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
