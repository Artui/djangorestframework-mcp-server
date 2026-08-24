# Recipes

Short, opinionated cookbook entries. Each one is a complete, runnable snippet
that solves a single concrete problem. Browse the list:

- [Expose a service](expose-a-service.md) — the smallest possible end-to-end.
- [Swap the session store](swap-session-store.md) — when the default cache
  isn't what you want.
- [Ship TOON for large lists](toon-output.md) — token-efficient tool output.
- [Add a custom permission](custom-permission.md) — beyond `ScopeRequired`.
- [Write an async-native auth backend](async-auth-backend.md) — `httpx`
  introspection against a remote IDP without blocking the event loop.
- [Add rate limiting](rate-limiting.md) — per-binding `MCPRateLimit` with
  the shipped `FixedWindowRateLimit` / `SlidingWindowRateLimit` or your own.
- [Multi-worker SSE with Redis](redis-sse-broker.md) — swap the in-memory
  broker for `RedisSSEBroker` so any ASGI worker can fan out push messages.
- [Resume SSE with Last-Event-ID](sse-replay-buffer.md) — opt-in per-session
  replay buffer so reconnecting clients catch up on missed events.
- [Honor `drf-spectacular` annotations](drf-spectacular.md) — feed
  `@extend_schema_serializer` / `@extend_schema_field` metadata into the
  MCP `inputSchema` automatically.
- [Selector tool with FilterSet](selector-tool-with-filterset.md) —
  expose a list-shaped read tool with `django-filter`, ordering, and
  pagination, generated `inputSchema` and all.
- [Render a DRF list as an interactive table](interactive-view.md) — MCP Apps:
  declare a `ui://` view, link a selector tool to it, and a host draws the
  results inline in the chat instead of reading JSON aloud.
- [Expose a polymorphic action as tools](polymorphic-action.md) — expand a
  drf-services `PolymorphicServiceSpec` into one flat tool per variant
  instead of a `anyOf` union.
- [Register tools from a shared spec registry](register-from-spec-registry.md)
  — when the same specs are exposed over MCP *and* another transport, declare
  them once in a `SpecRegistry` and bulk-register with `register_specs`.
- [Connect a Pydantic-AI agent over MCP](pydantic-ai-client.md) — drive this
  server from a `pydantic_ai.Agent` with `MCPToolset`, and when to reach for
  the in-process `SpecToolset` instead.
- [Migrate from `fastapi-mcp` / hand-rolled MCP](migrating.md) — step
  by step from a custom Django MCP view or a `fastapi-mcp` app.
