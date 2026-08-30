# Reference

Autodocs sourced from the package itself via `mkdocstrings`. Every entry is
generated from the live source — nothing duplicated by hand.

- [`MCPServer`](mcp-server.md) — the public entry point.
- [Protocol types](protocol.md) — JSON-RPC envelope, MCP message types, error codes.
- [Registries](registries.md) — `ToolRegistry`, `ResourceRegistry`, bindings, session stores.
- [Auth](auth.md) — backends, permissions, response builders.
- [Output](output.md) — `OutputFormat`, encoders, `build_tool_result`.
- [Testing](testing.md) — `assert_tool_result_conforms`, for a consumer's
  own suite.
- [Settings](settings.md) — every `REST_FRAMEWORK_MCP` key, its default,
  and the per-server override.
