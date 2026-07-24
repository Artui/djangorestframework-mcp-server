# Registries

Tool, resource, and prompt lookup, plus session storage and SSE infrastructure.

`ToolBinding` wraps a `ServiceSpec` (mutation tools);
`SelectorToolBinding` wraps a `SelectorSpec` and exposes the read-shaped
pipeline knobs — `filter_set` is read from the spec; `ordering_fields` /
`paginate` are binding-level MCP mechanics. The shared
`ToolRegistry` accepts either kind and is what `tools/list` and
`tools/call` iterate.

::: rest_framework_mcp.registry.types.tool_binding.ToolBinding
::: rest_framework_mcp.registry.types.selector_tool_binding.SelectorToolBinding
::: rest_framework_mcp.registry.types.url_kwarg.UrlKwarg
::: rest_framework_mcp.registry.tool_registry.ToolRegistry
::: rest_framework_mcp.registry.types.resource_binding.ResourceBinding
::: rest_framework_mcp.registry.resource_registry.ResourceRegistry
::: rest_framework_mcp.registry.types.prompt_binding.PromptBinding
::: rest_framework_mcp.registry.prompt_registry.PromptRegistry

## Bulk registration

`register_tools(server, definitions, *, selector_defaults=None, service_defaults=None)`
is an additive entry point for registering many tools in one call. Pass
a list of `ToolDefinition.service(...)` / `ToolDefinition.selector(...)`
instances plus per-kind `ServiceDefaults` / `SelectorDefaults` that fill
in fields each definition leaves as `None`. Returns the resulting
bindings in input order.

::: rest_framework_mcp.registry.register_tools.register_tools
::: rest_framework_mcp.registry.types.tool_definition.ToolDefinition
::: rest_framework_mcp.registry.types.service_defaults.ServiceDefaults
::: rest_framework_mcp.registry.types.selector_defaults.SelectorDefaults
::: rest_framework_mcp.constants.ToolKind

`ArgumentBinding` and `UnknownArguments` are re-exported from
`djangorestframework-services` (the transport-neutral `dispatch_spec` owns
these dispatch policies); import them from `rest_framework_mcp.constants`.

::: rest_framework_services.types.unknown_arguments.UnknownArguments
::: rest_framework_services.types.argument_binding.ArgumentBinding

## Selector-tool schema

Builds the merged `inputSchema` for selector tools — exposed for projects
that want to introspect filter / ordering / pagination property generation
outside of the registration flow. The FilterSet → JSON-Schema mapping is
delegated to `djangorestframework-services`'
[`filterset_to_json_schema`](https://github.com/Artui/djangorestframework-services),
so the filterable shape is described the same way across transports.

::: rest_framework_mcp.schema.selector_tool_schema.build_selector_tool_input_schema

## Session stores

::: rest_framework_mcp.transport.types.session_store.SessionStore
::: rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore
::: rest_framework_mcp.transport.django_cache_session_store.DjangoCacheSessionStore

## Server-initiated push

::: rest_framework_mcp.transport.types.sse_broker.SSEBroker
::: rest_framework_mcp.transport.in_memory_sse_broker.InMemorySSEBroker
::: rest_framework_mcp.transport.redis_sse_broker.RedisSSEBroker

## SSE replay (resume)

::: rest_framework_mcp.transport.types.sse_replay_buffer.SSEReplayBuffer
::: rest_framework_mcp.transport.in_memory_sse_replay_buffer.InMemorySSEReplayBuffer
::: rest_framework_mcp.transport.redis_sse_replay_buffer.RedisSSEReplayBuffer
