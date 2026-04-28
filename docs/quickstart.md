# Quickstart

Take an existing service and expose it as an MCP tool in under five minutes.

## 1. Install

```bash
pip install djangorestframework-mcp-server
```

If you don't already have `djangorestframework-services` configured, install it
too — `djangorestframework-mcp-server` registers `ServiceSpec` instances directly, with
no DRF view layer involved.

```bash
pip install djangorestframework djangorestframework-services
```

## 2. Define a service

A service is a regular Python callable that names the kwargs it wants. Nothing
about MCP is leaking in here — this same callable can serve an HTTP endpoint and
the MCP transport simultaneously.

```python title="invoices/services.py"
from invoices.models import Invoice


def create_invoice(*, data: dict) -> Invoice:
    """Create a new invoice from validated input data."""
    return Invoice.objects.create(
        number=data["number"], amount_cents=data["amount_cents"]
    )
```

## 3. Add input/output serializers

Use any DRF `Serializer`. They drive both validation (input) and the JSON Schema
that `tools/list` advertises.

```python title="invoices/serializers.py"
from rest_framework import serializers
from invoices.models import Invoice


class InvoiceInputSerializer(serializers.Serializer):
    number = serializers.CharField(max_length=32)
    amount_cents = serializers.IntegerField(min_value=0)


class InvoiceOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "amount_cents", "sent"]
```

## 4. Wire the MCP server

```python title="invoices/mcp.py"
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_mcp import MCPServer

from invoices.serializers import InvoiceInputSerializer, InvoiceOutputSerializer
from invoices.services import create_invoice

server = MCPServer(name="invoices")

server.register_tool(
    name="invoices.create",
    spec=ServiceSpec(
        service=create_invoice,
        input_serializer=InvoiceInputSerializer,
        output_serializer=InvoiceOutputSerializer,
    ),
    description="Create a new invoice.",
)
```

You can also use the decorator form — both register the same `ToolBinding`:

```python
@server.tool(
    name="invoices.create",
    input_serializer=InvoiceInputSerializer,
    output_serializer=InvoiceOutputSerializer,
)
def create_invoice(*, data):
    """Create a new invoice from validated input data."""
    return Invoice.objects.create(
        number=data["number"], amount_cents=data["amount_cents"]
    )
```

## 5. Mount the URLs

```python title="urls.py"
from django.urls import include, path

from invoices.mcp import server

urlpatterns = [
    path("mcp/", include(server.urls)),
]
```

That mounts:

- `POST /mcp/` — Streamable HTTP endpoint.
- `GET /mcp/.well-known/oauth-protected-resource` — RFC 9728 metadata.

## 6. Try it

In dev, the easiest way to drive the server is the official MCP Inspector:

```bash
npx @modelcontextprotocol/inspector --url http://localhost:8000/mcp/
```

Inspector lets you list tools, fill in arguments from the generated JSON Schema,
and watch the response. For automated checks, a single `curl` works too:

```bash
# 1) initialize → the response carries an Mcp-Session-Id header
curl -i http://localhost:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-11-25",
      "capabilities": {},
      "clientInfo": {"name": "curl", "version": "0"}
    }
  }'

# 2) re-use that session id on every subsequent call
SESSION="<paste from response header>"
curl http://localhost:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -H "Mcp-Protocol-Version: 2025-11-25" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "invoices.create",
      "arguments": {"number": "INV-1", "amount_cents": 100}
    }
  }'
```

For development, leave the default `AllowAnyBackend` in place. Before going to
production, swap to `DjangoOAuthToolkitBackend` (or your own) and set an
`ALLOWED_ORIGINS` allowlist — see [Authentication](auth.md) for the recipe.
