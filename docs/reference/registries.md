# Registries

Tool, resource, and prompt lookup, plus session storage and SSE infrastructure.

`ToolBinding` wraps a `ServiceSpec` (mutation tools);
`SelectorToolBinding` wraps a `SelectorSpec` and exposes the read-shaped
pipeline knobs — `filter_set` is read from the spec (and owns ordering,
via an `OrderingFilter`); `paginate` is the one binding-level MCP
mechanic. The shared
`ToolRegistry` accepts either kind and is what `tools/list` and
`tools/call` iterate.

::: rest_framework_mcp.registry.types.tool_binding.ToolBinding
::: rest_framework_mcp.registry.types.selector_tool_binding.SelectorToolBinding
::: rest_framework_services.types.url_kwarg.UrlKwarg
::: rest_framework_services.types.query_param.QueryParam
::: rest_framework_mcp.registry.tool_registry.ToolRegistry
::: rest_framework_mcp.registry.types.resource_binding.ResourceBinding
::: rest_framework_mcp.registry.resource_registry.ResourceRegistry
::: rest_framework_mcp.constants.ResourceEncoding
::: rest_framework_mcp.registry.types.prompt_binding.PromptBinding
::: rest_framework_mcp.registry.prompt_registry.PromptRegistry

## Interactive views (MCP Apps)

`MCPServer.register_ui_resource(...)` declares an HTML view for an MCP host to
render inline in the chat. The view is an ordinary `ResourceBinding` with the
Apps mime type, `TEXT` encoding, and a `_meta` bundle built from
`UIResourceMeta`; `UIToolMeta` then links a tool to it, so the host renders that
tool's result inside the view. See
[Interactive views](../concepts.md#interactive-views-mcp-apps) for the
host/server split, the three refused-link cases, and the keep-tenant-data-out
rule.

::: rest_framework_mcp.registry.types.ui_resource_meta.UIResourceMeta
::: rest_framework_mcp.registry.types.ui_csp.UICsp
::: rest_framework_mcp.constants.UIPermission
::: rest_framework_mcp.registry.types.ui_tool_meta.UIToolMeta
::: rest_framework_mcp.constants.UIVisibility

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

## Chain tools

`ChainStep` is one step of a `register_chain_tool` sequence — an alias, a
`ServiceSpec` / `SelectorSpec`, and an `inputs` callable. That callable receives
a `ChainContext`, which exposes the validated tool arguments as `ctx.args` and
any prior step's output as `ctx[alias]`. See
[Chain specs into one tool](../recipes/chain-tools.md).

::: rest_framework_mcp.registry.types.chain_step.ChainStep
::: rest_framework_mcp.registry.types.chain_context.ChainContext
::: rest_framework_mcp.registry.types.chain_tool_binding.ChainToolBinding

## Selector-tool schema

Builds the merged `inputSchema` for selector tools — exposed for projects
that want to introspect property generation outside of the registration flow.
The selector's own signature (its declared parameters and an `**extras:
Unpack[TypedDict]`, plus the FilterSet fields) is reflected via
`djangorestframework-services`'
[`spec_to_json_schema`](https://github.com/Artui/djangorestframework-services)
— the same reflection the Pydantic-AI toolset consumes — with ordering /
pagination knobs and any explicit `input_serializer` / `UrlKwarg` layered on
top, so the shape is described the same way across transports.

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
