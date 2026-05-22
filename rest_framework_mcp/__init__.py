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
    ArgumentBinding,
    OutputFormat,
    ToolKind,
    UnknownArguments,
)
from rest_framework_mcp.protocol.types.prompt_argument import PromptArgument
from rest_framework_mcp.protocol.types.prompt_message import PromptMessage
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.register_tools import register_tools
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.selector_defaults import SelectorDefaults
from rest_framework_mcp.registry.types.service_defaults import ServiceDefaults
from rest_framework_mcp.registry.types.tool_definition import ToolDefinition
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.server.types.mcp_service_view import MCPServiceView
from rest_framework_mcp.transport.django_cache_session_store import DjangoCacheSessionStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from rest_framework_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from rest_framework_mcp.transport.in_memory_sse_replay_buffer import InMemorySSEReplayBuffer
from rest_framework_mcp.transport.types.session_store import SessionStore
from rest_framework_mcp.transport.types.sse_broker import SSEBroker
from rest_framework_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer
from rest_framework_mcp.version import __version__

__all__ = [
    "ArgumentBinding",
    "CreateService",
    "DRFPermissionAdapter",
    "DeleteService",
    "DjangoCacheSessionStore",
    "DjangoPermRequired",
    "InMemorySSEBroker",
    "InMemorySSEReplayBuffer",
    "InMemorySessionStore",
    "ListSelector",
    "MCPAuthBackend",
    "MCPPermission",
    "MCPServer",
    "MCPServiceView",
    "OutputFormat",
    "PromptArgument",
    "PromptMessage",
    "PromptRegistry",
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
    "ToolDefinition",
    "ToolKind",
    "ToolRegistry",
    "UnknownArguments",
    "UpdateService",
    "register_tools",
    "__version__",
]
