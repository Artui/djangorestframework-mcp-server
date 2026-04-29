# Registries

Tool, resource, and prompt lookup, plus session storage.

`ToolBinding` wraps a `ServiceSpec` (mutation tools);
`SelectorToolBinding` wraps a `SelectorSpec` and carries the read-shaped
pipeline knobs (`filter_set`, `ordering_fields`, `paginate`). The shared
`ToolRegistry` accepts either kind and is what `tools/list` and
`tools/call` iterate.

::: rest_framework_mcp.registry.tool_binding.ToolBinding
::: rest_framework_mcp.registry.selector_tool_binding.SelectorToolBinding
::: rest_framework_mcp.registry.tool_registry.ToolRegistry
::: rest_framework_mcp.registry.resource_binding.ResourceBinding
::: rest_framework_mcp.registry.resource_registry.ResourceRegistry
::: rest_framework_mcp.registry.prompt_binding.PromptBinding
::: rest_framework_mcp.registry.prompt_registry.PromptRegistry

## Selector-tool schema

Helpers that build the merged `inputSchema` for selector tools — exposed
for projects that want to introspect filter / ordering / pagination
property generation outside of the registration flow.

::: rest_framework_mcp.schema.filterset_schema.filterset_to_schema_properties
::: rest_framework_mcp.schema.selector_tool_schema.build_selector_tool_input_schema

## Session stores

::: rest_framework_mcp.transport.session_store.SessionStore
::: rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore
::: rest_framework_mcp.transport.django_cache_session_store.DjangoCacheSessionStore

## Server-initiated push

::: rest_framework_mcp.transport.sse_broker.SSEBroker
::: rest_framework_mcp.transport.in_memory_sse_broker.InMemorySSEBroker
::: rest_framework_mcp.transport.redis_sse_broker.RedisSSEBroker

## SSE replay (resume)

::: rest_framework_mcp.transport.sse_replay_buffer.SSEReplayBuffer
::: rest_framework_mcp.transport.in_memory_sse_replay_buffer.InMemorySSEReplayBuffer
::: rest_framework_mcp.transport.redis_sse_replay_buffer.RedisSSEReplayBuffer
