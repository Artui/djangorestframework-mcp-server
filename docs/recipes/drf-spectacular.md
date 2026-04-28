# Honor `drf-spectacular` annotations on `inputSchema`

If your project already uses [`drf-spectacular`](https://drf-spectacular.readthedocs.io/)
to generate OpenAPI for the HTTP transport, the same `@extend_schema_*`
decorators feed the MCP `inputSchema` automatically — no separate
annotation layer needed. The integration is **opt-in via install**:
spectacular is detected by checking for `_spectacular_annotation` on the
serializer class, so projects that don't have spectacular installed see
zero overhead.

## Install

```bash
pip install "djangorestframework-mcp-server[spectacular]"
```

This pulls in `drf-spectacular>=0.27`. No app config change required —
the MCP package only reads the metadata spectacular has already attached
to your serializers.

## What's honored

### `@extend_schema_serializer` (class-level)

| Decorator argument | Effect on the MCP `inputSchema` |
| --- | --- |
| `exclude_fields` | Drops the named properties and removes them from `required`. |
| `deprecate_fields` | Sets `"deprecated": true` on the named properties. |
| `examples` | Aggregates the `OpenApiExample.value` payloads into the JSON Schema `examples` array (placeholder examples without a `value=` are filtered out). |
| `component_name` / `extensions` | Ignored — MCP inlines the schema per-tool, no OpenAPI componentisation. |

```python
from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers


@extend_schema_serializer(
    exclude_fields=["internal_id"],
    deprecate_fields=["legacy"],
    examples=[
        OpenApiExample(
            "Typical invoice",
            value={"amount": 4200, "currency": "USD"},
        ),
    ],
)
class InvoiceInput(serializers.Serializer):
    amount = serializers.IntegerField()
    currency = serializers.ChoiceField(choices=[("USD", "USD"), ("EUR", "EUR")])
    legacy = serializers.CharField(required=False)
    internal_id = serializers.IntegerField(required=False)
```

The MCP `inputSchema` for any tool taking `InvoiceInput` will:

- Omit `internal_id` entirely.
- Carry `"deprecated": true` on `legacy`.
- Surface `examples: [{"amount": 4200, "currency": "USD"}]`.

### `@extend_schema_field` (field-level)

When you decorate a custom `Field` subclass with `@extend_schema_field({...})`,
the dict you pass replaces the auto-derived JSON Schema fragment for any
field of that type:

```python
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers


@extend_schema_field({"type": "string", "format": "iban", "minLength": 15})
class IBANField(serializers.CharField):
    """Custom field with a richer surface than plain ``CharField``."""


class TransferInput(serializers.Serializer):
    from_account = IBANField()
    to_account = IBANField()
    amount = serializers.IntegerField()
```

The two `IBAN` properties land in `inputSchema` as
`{"type": "string", "format": "iban", "minLength": 15}`.

### What's *not* honored

- **`@extend_schema_field` with a non-dict value** (an `OpenApiTypes`
  enum, a serializer class) — falls through to the auto-derived schema
  rather than guessing how to translate spectacular-internal types to
  JSON Schema. If you need the enum form, pass a dict instead.
- **`@extend_schema_field` on a `SerializerMethodField`'s `get_*`
  method** — would require introspection of the parent serializer at
  schema-build time. Not supported in v1.
- **`@extend_schema` on a view** — the MCP package doesn't dispatch
  through DRF views, so view-level overrides have no place to attach.

## Verifying the integration

The MCP `tools/list` response carries the `inputSchema` directly. Hit it
from your test suite:

```python
response = client.post(
    "/mcp/",
    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
)
schemas = {tool["name"]: tool["inputSchema"] for tool in response.json()["result"]["tools"]}
assert "internal_id" not in schemas["create-invoice"]["properties"]
```

Or invoke `build_input_schema` directly in a unit test — it's a thin
public helper (`from rest_framework_mcp.schema.input_schema import
build_input_schema`).
