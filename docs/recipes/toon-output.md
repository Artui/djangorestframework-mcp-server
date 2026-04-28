# Ship TOON for large lists

[TOON](https://github.com/iaminawe/python-toon) is a token-oriented object
notation that compresses uniform list-of-objects payloads dramatically. For
tools that return long lists of similar dicts (catalog listings, search
results, log slices), TOON cuts token usage in half or better.

```bash
pip install "djangorestframework-mcp-server[toon]"
```

Pick the format per binding:

```python
from rest_framework_mcp import OutputFormat

server.register_tool(
    name="invoices.list",
    spec=ServiceSpec(service=list_invoices, output_serializer=InvoiceOutputSerializer),
    output_format=OutputFormat.AUTO,   # picks TOON when the payload is uniform
)
```

`OutputFormat.AUTO` uses TOON when the payload is a non-empty list whose
elements all share the same key set, and JSON otherwise. Force it explicitly
with `OutputFormat.TOON` if you always want TOON, accepting the JSON fallback
warning when the extra is missing.

`structuredContent` is always JSON — only the human-readable `content[0]` text
block changes shape. Clients that don't understand TOON natively still see a
fenced code block:

```text
# format: toon
```toon
…compact representation…
```
```

Per-call override is supported too: a client can pass
`{"outputFormat": "json"}` in `tools/call.params` to override the binding's
default for a single invocation.
