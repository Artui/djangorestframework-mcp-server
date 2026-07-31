# Protocol types

JSON-RPC envelope, MCP message types, and error codes.

## JSON-RPC

::: rest_framework_mcp.protocol.types.json_rpc_request.JsonRpcRequest
::: rest_framework_mcp.protocol.types.json_rpc_notification.JsonRpcNotification
::: rest_framework_mcp.protocol.types.json_rpc_response.JsonRpcResponse
::: rest_framework_mcp.protocol.types.json_rpc_error.JsonRpcError
::: rest_framework_mcp.constants.JsonRpcErrorCode
::: rest_framework_mcp.protocol.parse_message.parse_message

## Display metadata

::: rest_framework_mcp.protocol.types.icon.Icon
::: rest_framework_mcp.constants.IconTheme

## Result envelope

::: rest_framework_mcp.constants.ResultType
::: rest_framework_mcp.constants.CacheScope

## Discovery and the initialize handshake

::: rest_framework_mcp.protocol.types.discover_result.DiscoverResult
::: rest_framework_mcp.protocol.types.implementation.Implementation
::: rest_framework_mcp.protocol.types.client_capabilities.ClientCapabilities
::: rest_framework_mcp.protocol.types.server_capabilities.ServerCapabilities
::: rest_framework_mcp.protocol.types.initialize_params.InitializeParams
::: rest_framework_mcp.protocol.types.initialize_result.InitializeResult

## Tools

::: rest_framework_mcp.protocol.types.tool.Tool
::: rest_framework_mcp.protocol.types.tool_content_block.ToolContentBlock
::: rest_framework_mcp.constants.ToolContentKind
::: rest_framework_mcp.protocol.types.tool_result.ToolResult

## Resources

::: rest_framework_mcp.protocol.types.resource.Resource
::: rest_framework_mcp.protocol.types.resource_template.ResourceTemplate
::: rest_framework_mcp.protocol.types.resource_contents.ResourceContents

## Prompts

::: rest_framework_mcp.protocol.types.prompt.Prompt
::: rest_framework_mcp.protocol.types.prompt_argument.PromptArgument
::: rest_framework_mcp.protocol.types.prompt_message.PromptMessage
::: rest_framework_mcp.protocol.types.get_prompt_result.GetPromptResult

## Completion

::: rest_framework_mcp.protocol.types.completion.Completion

## Tasks

The `io.modelcontextprotocol/tasks` extension. See
[Long-running work](../concepts.md#long-running-work-tasks) for how to wire it
up; these are the types.

::: rest_framework_mcp.protocol.types.task.Task
::: rest_framework_mcp.constants.TaskStatus
::: rest_framework_mcp.constants.TaskPolicy
::: rest_framework_mcp.tasks.types.task_store.TaskStore
::: rest_framework_mcp.tasks.types.task_executor.TaskExecutor
::: rest_framework_mcp.tasks.types.task_record.TaskRecord
::: rest_framework_mcp.tasks.django_cache_task_store.DjangoCacheTaskStore
::: rest_framework_mcp.tasks.in_memory_task_store.InMemoryTaskStore
