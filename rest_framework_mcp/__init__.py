# Re-exports from the sister package — provided here so MCP consumers don't
# need to know which sub-package each symbol lives in. The dependency on
# ``djangorestframework-services`` is mandatory anyway, so this is purely an
# ergonomic convenience.
from rest_framework_services.selectors.list_selector import ListSelector
from rest_framework_services.selectors.retrieve_selector import RetrieveSelector
from rest_framework_services.services.create_service import CreateService
from rest_framework_services.services.delete_service import DeleteService
from rest_framework_services.services.update_service import UpdateService
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.service_view import ServiceView

from rest_framework_mcp.auth.permissions.django_perm_required import DjangoPermRequired
from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.auth.permissions.scope_required import ScopeRequired
from rest_framework_mcp.auth.permissions.types.mcp_permission import MCPPermission
from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import (
    UI_EXTENSION_ID,
    UI_RESOURCE_MIME_TYPE,
    ArgumentBinding,
    IconTheme,
    JsonRpcErrorCode,
    OutputFormat,
    ResourceEncoding,
    ToolContentKind,
    ToolKind,
    UIPermission,
    UIVisibility,
    UnknownArguments,
)
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.prompt_argument import PromptArgument
from rest_framework_mcp.protocol.types.prompt_message import PromptMessage
from rest_framework_mcp.protocol.types.resource_contents import ResourceContents
from rest_framework_mcp.protocol.types.tool_content_block import ToolContentBlock
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.register_tools import register_tools
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.chain_context import ChainContext
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.selector_defaults import SelectorDefaults
from rest_framework_mcp.registry.types.service_defaults import ServiceDefaults
from rest_framework_mcp.registry.types.tool_definition import ToolDefinition
from rest_framework_mcp.registry.types.ui_csp import UICsp
from rest_framework_mcp.registry.types.ui_resource_meta import UIResourceMeta
from rest_framework_mcp.registry.types.ui_tool_meta import UIToolMeta
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.transport.django_cache_session_store import DjangoCacheSessionStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from rest_framework_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from rest_framework_mcp.transport.in_memory_sse_replay_buffer import InMemorySSEReplayBuffer
from rest_framework_mcp.transport.types.session_store import SessionStore
from rest_framework_mcp.transport.types.sse_broker import SSEBroker
from rest_framework_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer
from rest_framework_mcp.version import __version__

__all__ = [
    "UI_EXTENSION_ID",
    "UI_RESOURCE_MIME_TYPE",
    "ArgumentBinding",
    "ChainContext",
    "ChainStep",
    "CreateService",
    "DRFPermissionAdapter",
    "DeleteService",
    "DjangoCacheSessionStore",
    "DjangoPermRequired",
    "InMemorySSEBroker",
    "InMemorySSEReplayBuffer",
    "Icon",
    "IconTheme",
    "InMemorySessionStore",
    "JsonRpcError",
    "JsonRpcErrorCode",
    "ListSelector",
    "MCPAuthBackend",
    "MCPPermission",
    "MCPServer",
    "OutputFormat",
    "PromptArgument",
    "PromptMessage",
    "PromptRegistry",
    "ResourceContents",
    "ResourceEncoding",
    "ResourceRegistry",
    "RetrieveSelector",
    "SSEBroker",
    "SSEReplayBuffer",
    "ScopeRequired",
    "SelectorDefaults",
    "SelectorKind",
    "SelectorSpec",
    "ServiceDefaults",
    "ServiceSpec",
    "ServiceView",
    "SessionStore",
    "TokenInfo",
    "ToolContentBlock",
    "ToolContentKind",
    "ToolDefinition",
    "ToolKind",
    "ToolRegistry",
    "UICsp",
    "UIPermission",
    "UIResourceMeta",
    "UIToolMeta",
    "UIVisibility",
    "UnknownArguments",
    "UpdateService",
    "QueryParam",
    "UrlKwarg",
    "register_tools",
    "__version__",
]
