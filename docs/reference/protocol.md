# Protocol types

JSON-RPC envelope, MCP message types, and error codes.

## JSON-RPC

::: rest_framework_mcp.protocol.types.json_rpc_request.JsonRpcRequest
::: rest_framework_mcp.protocol.types.json_rpc_notification.JsonRpcNotification
::: rest_framework_mcp.protocol.types.json_rpc_response.JsonRpcResponse
::: rest_framework_mcp.protocol.types.json_rpc_error.JsonRpcError
::: rest_framework_mcp.constants.JsonRpcErrorCode
::: rest_framework_mcp.protocol.parse_message.parse_message

## Initialize handshake

::: rest_framework_mcp.protocol.types.implementation.Implementation
::: rest_framework_mcp.protocol.types.client_capabilities.ClientCapabilities
::: rest_framework_mcp.protocol.types.server_capabilities.ServerCapabilities
::: rest_framework_mcp.protocol.types.initialize_params.InitializeParams
::: rest_framework_mcp.protocol.types.initialize_result.InitializeResult

## Tools

::: rest_framework_mcp.protocol.types.tool.Tool
::: rest_framework_mcp.protocol.types.tool_content_block.ToolContentBlock
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
