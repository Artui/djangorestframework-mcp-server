# Migrating to `djangorestframework-mcp-server`

This page covers two common starting points: a hand-rolled Django MCP
server (often a single `View` that hand-parses JSON-RPC) and a
FastAPI app exposed via [`fastapi-mcp`](https://github.com/tadata-org/fastapi_mcp).

The migration target is the same in both cases: services and selectors
registered against an `MCPServer` and mounted at a single URL.

## From a hand-rolled MCP view

A typical hand-rolled implementation looks like this — one Django view
that parses JSON-RPC, dispatches by method name, and serialises a
response:

```python
# Before — custom DIY
class MCPView(View):
    def post(self, request):
        msg = json.loads(request.body)
        if msg["method"] == "tools/list":
            return JsonResponse({"jsonrpc": "2.0", "id": msg["id"],
                                 "result": {"tools": [
                                     {"name": "create_invoice",
                                      "inputSchema": {...},
                                      "description": "..."},
                                 ]}})
        if msg["method"] == "tools/call":
            args = msg["params"]["arguments"]
            invoice = Invoice.objects.create(
                number=args["number"], amount_cents=args["amount_cents"],
            )
            return JsonResponse({"jsonrpc": "2.0", "id": msg["id"],
                                 "result": {"structuredContent": {
                                     "id": invoice.id,
                                     "number": invoice.number,
                                     "amount_cents": invoice.amount_cents,
                                 }}})
        return JsonResponse({"jsonrpc": "2.0", "id": msg["id"],
                             "error": {"code": -32601, "message": "method not found"}},
                            status=400)
```

Common gaps in DIY servers — each of which the package handles for you:

- **Streamable-HTTP transport** (sessions, `MCP-Protocol-Version` /
  `MCP-Session-Id` / `Origin` validation, body-size cap, 401
  challenges, notification 202) — required by the MCP spec.
- **Schema introspection** — generating the `inputSchema` /
  `outputSchema` from DRF serializers / dataclasses, so `tools/list` is
  trustworthy.
- **Dispatch wiring** — input validation, kwarg-pool resolution,
  output serialization, error mapping (DRF / `ServiceError` →
  JSON-RPC errors).
- **Auth + rate limits + permissions** — DOT integration, scope-aware
  challenges, per-binding `MCPPermission` and `MCPRateLimit`.
- **Async + SSE + replay** — JSON dispatch in a sync stack, async
  dispatch under ASGI, GET-side server-initiated push, reconnect via
  `Last-Event-ID`.

### Step 1 — Pull the business logic into services and selectors

Mutations become **services**, reads become **selectors**. The
function bodies stay the same; what changes is that the entry points
are now plain Python (no `request` parsing, no JSON shaping) and a
`ServiceSpec` / `SelectorSpec` carries the contract.

```python
# After — a plain service callable
from rest_framework_services.exceptions.service_error import ServiceError


def create_invoice(*, data: dict) -> Invoice:
    return Invoice.objects.create(
        number=data["number"], amount_cents=data["amount_cents"],
    )


def list_invoices() -> QuerySet[Invoice]:
    return Invoice.objects.all()
```

### Step 2 — Define DRF serializers (or dataclasses)

The package generates `inputSchema` / `outputSchema` from these. If you
already have DRF serializers, reuse them verbatim.

```python
class InvoiceInput(serializers.Serializer):
    number = serializers.CharField(max_length=32)
    amount_cents = serializers.IntegerField(min_value=0)


class InvoiceOutput(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "amount_cents", "sent"]
```

### Step 3 — Register on an `MCPServer`

Replace the custom view with a single `MCPServer` instance:

```python
# After — wired through MCPServer
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_mcp import MCPServer

server = MCPServer(name="invoicing")

server.register_service_tool(
    name="invoices.create",
    spec=ServiceSpec(
        service=create_invoice,
        input_serializer=InvoiceInput,
        output_serializer=InvoiceOutput,
    ),
    description="Create a new invoice.",
)

server.register_selector_tool(
    name="invoices.list",
    spec=SelectorSpec(selector=list_invoices, output_serializer=InvoiceOutput),
    paginate=True,
)
```

### Step 4 — Drop the URL conf in

```python
# urls.py
urlpatterns = [path("mcp/", include(server.urls))]
```

That's it. Every MCP method (`initialize`, `tools/list`, `tools/call`,
`resources/*`, `prompts/*`, `ping`) is handled. Auth, sessions, origin
allowlisting, and the rest of the wire contract are routed through the
package's view.

### Step 5 — Delete the DIY plumbing

Once the new endpoint passes Inspector / your end-to-end suite, delete:

- the custom `View` and its URL
- the custom JSON-RPC dispatcher
- any hand-written input validation that the serializer now covers
- error-mapping code that translated exceptions to JSON-RPC errors
  (the package does this at the boundary)

## From `fastapi-mcp`

`fastapi-mcp` decorates FastAPI route handlers and exposes them as MCP
tools. The migration to `djangorestframework-mcp-server` is mostly
mechanical because both packages think in terms of "callable + schema":

| `fastapi-mcp`                                              | `djangorestframework-mcp-server`                                       |
|------------------------------------------------------------|------------------------------------------------------------------------|
| `@mcp.tool` on a FastAPI handler                           | `register_service_tool(...)` or `@server.service_tool`                  |
| Pydantic input model                                       | DRF `Serializer` (or `@dataclass` — the package generates the schema)   |
| Pydantic output model                                      | DRF `Serializer` on `ServiceSpec.output_serializer`                     |
| `@mcp.resource("uri://{var}")`                             | `register_resource(uri_template="uri://{var}", selector=SelectorSpec(...))` |
| `@mcp.prompt`                                              | `register_prompt(name=..., render=...)`                                 |
| FastAPI's dependency injection (`request`, `db`, etc.)     | The kwarg pool — declare the kwargs you need on the callable; `resolve_callable_kwargs` handles the rest |
| Streamable-HTTP transport implemented inside `fastapi-mcp` | Implemented inside `djangorestframework-mcp-server`; same wire shape   |

### Mapping example

```python
# fastapi-mcp
@mcp.tool
async def create_invoice(data: InvoiceInputModel) -> InvoiceOutputModel:
    invoice = await Invoice.objects.acreate(
        number=data.number, amount_cents=data.amount_cents,
    )
    return InvoiceOutputModel.from_orm(invoice)
```

```python
# djangorestframework-mcp-server
async def create_invoice(*, data: dict) -> Invoice:
    return await Invoice.objects.acreate(
        number=data["number"], amount_cents=data["amount_cents"],
    )


server.register_service_tool(
    name="create_invoice",
    spec=ServiceSpec(
        service=create_invoice,
        input_serializer=InvoiceInput,
        output_serializer=InvoiceOutput,
    ),
)
```

The `async def` works as-is — the dispatch layer detects async
callables and awaits them natively when mounted under
`server.async_urls`.

### Pydantic → DRF serializer

Pydantic v2 and DRF serializers have similar shapes. For most fields a
mechanical translation is enough:

| Pydantic                   | DRF                                            |
|----------------------------|------------------------------------------------|
| `field: str = Field(...)`  | `field = serializers.CharField()`              |
| `field: int = 0`           | `field = serializers.IntegerField(default=0)`  |
| `field: list[str]`         | `field = serializers.ListField(child=serializers.CharField())` |
| `field: SomeModel`         | nested `serializers.Serializer` subclass       |
| `Field(..., min_length=N)` | `CharField(min_length=N)`                      |

If the rewrite is large, fall back to **dataclasses**: every service
callable can take a bare `@dataclass` for `data`, and the package
auto-wraps it in DRF's `DataclassSerializer`. Annotate every field
concretely (`Any` doesn't introspect — see
[`docs/concepts.md`](../concepts.md)).

### Ports & deployment

- FastAPI runs under `uvicorn` directly. Django needs an ASGI server
  too — `uvicorn invoicing.asgi:application` is fine.
- `fastapi-mcp` mounts on a path of your choice; this package mounts
  via `path("mcp/", include(server.urls))`. Both are configurable.
- Auth: `fastapi-mcp` typically piggybacks on FastAPI's dependency
  graph for tokens. Replace with an `MCPAuthBackend` — the
  `DjangoOAuthToolkitBackend` covers most BYO-AS scenarios, or
  implement the Protocol with `httpx` against a remote IDP (see
  [`docs/recipes/async-auth-backend.md`](async-auth-backend.md)).

## Worked example to copy from

[`examples/invoicing/`](https://github.com/Artui/djangorestframework-mcp-server/tree/main/examples/invoicing)
is a full Django project that exercises `register_service_tool`,
`register_selector_tool` (with `FilterSet`, ordering, pagination),
`register_resource` (templated URI), and `register_prompt`. It's a
faster way to see the end-to-end shape than reading the docs alone.
