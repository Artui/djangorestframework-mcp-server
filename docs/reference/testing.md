# Testing

Helpers for a **consumer's** test suite, shipped in the wheel and importable
from `rest_framework_mcp.testing`. Nothing here is imported by the package at
runtime, and the dependency they need is an extra:

```bash
pip install "djangorestframework-mcp-server[test]"
```

## Does a result match the schema the server advertised?

The package already refuses a binding whose *settings* disagree —
`include_output_schema=True` with `include_structured_content=False` raises at
registration, because the MCP spec requires conforming `structuredContent`
whenever `outputSchema` is declared (see
[Omitting `structuredContent` and `outputSchema`](../concepts.md#omitting-structuredcontent-and-outputschema)).

That is coherence, decided from the configuration alone. Whether a real
response *conforms* is a different question, and the check a suite reaches for
on its own answers a third one:

```python
advertised = tool["outputSchema"]["items"]["properties"]
assert set(advertised) == set(result["structuredContent"][0])
```

Comparing key sets catches a field that vanished. It passes unchanged when a
property advertised as `integer` arrives as a string, or a `date-time` arrives
as `"soon"` — which is what a typed client actually breaks on, and what an
output serializer's `to_representation` override introduces without touching a
single key.

`assert_tool_result_conforms` validates the payload against the schema, types
and formats included, and names each disagreement:

```python
from rest_framework_mcp.testing import assert_tool_result_conforms

page = server.list_tools(user=user)
tool = next(entry for entry in page["tools"] if entry["name"] == "invoices.list")
result = await server.acall_tool("invoices.list", {}, user=user)

assert_tool_result_conforms(tool, result)
```

```text
AssertionError: Tool 'invoices.list' returned 'structuredContent' that does not
conform to the 'outputSchema' it advertises (2 problems):
  - $[0].amount_cents: advertised type 'integer', got string '1240.00'
  - $[1]: 'number' is a required property
```

Both arguments are plain mappings off the wire — one `tools/list` entry and one
`tools/call` result — so the same call works against
[`list_tools` / `acall_tool`](../concepts.md#full-in-process-transport-acall_tool-list_tools),
an HTTP round trip, or a client library.

A tool that advertises no schema is a **failure**, not a pass: there would be
nothing to conform to, and an assertion that holds for every possible result is
the thing this replaces.

!!! note "How much of a format is checked depends on the install"

    `jsonschema` registers a format checker only when the library that performs
    it is importable, so the `test` extra pulls those in. Install it as
    `jsonschema` alone and `"format": "date-time"` is not checked — and this
    still passes.

There is deliberately **no setting that forces** every tool to advertise a
schema. For a service whose response shape is context-dependent, compelling
advertisement is the wrong default; the existing on/off knob plus per-binding
overrides is the right shape. Verification was the missing piece, not
compulsion.

::: rest_framework_mcp.testing.assert_tool_result_conforms.assert_tool_result_conforms
