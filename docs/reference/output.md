# Output

`OutputFormat`, encoders, and the `ToolResult` builder.

A tool's result has two halves and they are toggled independently:
`structuredContent` on the call result, and `outputSchema` on the `tools/list`
entry. The MCP spec imposes one asymmetric rule between them — **a tool that
advertises `outputSchema` must return conforming `structuredContent`**, while
the reverse is allowed — so they are not a single switch.

Both are on by default, server-wide via
`REST_FRAMEWORK_MCP["INCLUDE_STRUCTURED_CONTENT"]` and
`["INCLUDE_OUTPUT_SCHEMA"]`, and per tool via `include_structured_content=` and
`include_output_schema=` at registration. The asymmetric rule is enforced:
`include_output_schema=True` with `include_structured_content=False` raises at
registration rather than advertising a schema nothing will satisfy.

That is a check on the *settings*, not on a response. To assert that a real
result satisfies the schema its tool advertised — types and formats, not only
the property names — see
[`assert_tool_result_conforms`](testing.md#does-a-result-match-the-schema-the-server-advertised).

See [Omitting `structuredContent` and `outputSchema`](../concepts.md#omitting-structuredcontent-and-outputschema)
for when to turn either off, and
[the schema an agent sees](../concepts.md#what-an-agent-sees) for what shapes
the advertised schema.

::: rest_framework_mcp.constants.OutputFormat
::: rest_framework_mcp.output.encode_json.encode_json
::: rest_framework_mcp.output.encode_toon.encode_toon
::: rest_framework_mcp.output.tool_result.build_tool_result
