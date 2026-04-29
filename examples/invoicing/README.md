# `invoicing/` — service tools, selector tool, resource, prompt

A representative Django project that exposes an `Invoice` model over
MCP using every public registration surface of
`djangorestframework-mcp-server`:

| Surface                        | Demonstrated by                                              |
|--------------------------------|--------------------------------------------------------------|
| `register_service_tool`        | `invoices.create` — creates a new invoice (atomic mutation). |
| `register_service_tool`        | `invoices.mark_sent` — flips the `sent` flag.                |
| `register_selector_tool`       | `invoices.list` — list with `FilterSet`, ordering, pagination.|
| `register_resource`            | `invoice` — single invoice by PK via `invoices://{pk}` URI.  |
| `register_prompt`              | `compose_invoice_email` — render an email body for an invoice.|

The whole MCP wiring lives in `invoices/mcp.py`. Models, serializers,
filters, services, selectors are split into their own modules so each
piece is small.

## Layout

```
invoicing/
├── README.md                ← you are here
├── manage.py                ← standard Django entry point
├── invoicing/               ← Django project package
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── asgi.py
└── invoices/                ← single Django app
    ├── __init__.py
    ├── apps.py
    ├── models.py
    ├── serializers.py
    ├── filters.py
    ├── services.py
    ├── selectors.py
    ├── mcp.py               ← MCPServer factory + registrations
    └── migrations/
        ├── __init__.py
        └── 0001_initial.py
```

## Run

```bash
cd examples/invoicing

# Install the package (with the [filter] extra needed for the selector tool)
uv pip install -e "../.." "../..[filter]"

# Database
python manage.py migrate

# Serve
python manage.py runserver
```

## Drive it

`initialize` to get a session id:

```bash
curl -i -X POST http://localhost:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Mcp-Protocol-Version: 2025-11-25' \
  -H 'Origin: http://localhost' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-11-25","capabilities":{},
                 "clientInfo":{"name":"curl","version":"0"}}}'
# → Mcp-Session-Id: <uuid>
```

Export the session id and walk through the surfaces:

```bash
SID=<paste from above>
H='-H Content-Type: application/json
   -H Mcp-Protocol-Version: 2025-11-25
   -H Origin: http://localhost
   -H Mcp-Session-Id: '"$SID"

# Create three invoices
for n in A B C; do
  curl -s -X POST http://localhost:8000/mcp/ $H \
    -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",
         \"params\":{\"name\":\"invoices.create\",
                     \"arguments\":{\"number\":\"INV-$n\",\"amount_cents\":${RANDOM:0:4}00}}}"
done

# List with the selector tool — filter, sort, paginate
curl -s -X POST http://localhost:8000/mcp/ $H \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
       "params":{"name":"invoices.list",
                 "arguments":{"sent":false,"ordering":"-created_at","page":1,"limit":2}}}'

# Mark one as sent
curl -s -X POST http://localhost:8000/mcp/ $H \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call",
       "params":{"name":"invoices.mark_sent","arguments":{"pk":1}}}'

# Read it back via the resource template
curl -s -X POST http://localhost:8000/mcp/ $H \
  -d '{"jsonrpc":"2.0","id":5,"method":"resources/read",
       "params":{"uri":"invoices://1"}}'

# Render the email prompt
curl -s -X POST http://localhost:8000/mcp/ $H \
  -d '{"jsonrpc":"2.0","id":6,"method":"prompts/get",
       "params":{"name":"compose_invoice_email","arguments":{"pk":"1"}}}'
```

## Where the patterns are documented

- Service tool basics — [`docs/recipes/expose-a-service.md`](../../docs/recipes/expose-a-service.md)
- Selector tool with FilterSet — [`docs/recipes/selector-tool-with-filterset.md`](../../docs/recipes/selector-tool-with-filterset.md)
- Concepts (`ServiceSpec` / `SelectorSpec`, dispatch flow) — [`docs/concepts.md`](../../docs/concepts.md)
- Auth (this example uses `AllowAnyBackend`; production swaps in DOT) — [`docs/auth.md`](../../docs/auth.md)
