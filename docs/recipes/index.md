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
