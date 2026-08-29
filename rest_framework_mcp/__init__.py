"""Public API for ``rest_framework_mcp``.

Nine drf-services symbols used to be re-exported here -- ``ServiceSpec``,
``SelectorSpec``, ``SelectorKind``, ``ServiceView`` and the five service /
selector protocols -- on the reasoning that a consumer should not have to know
which sub-package each lives in, the dependency being mandatory anyway.

They are gone, because the convenience pinned this package's **public** API to
the sister package's **internal** layout: every one of those imports named a
module path (``rest_framework_services.types.service_spec``) that drf-services
is free to move, and moving one would have broken an import from *here*.
Import them from ``rest_framework_services``, which is where they are declared
and where that package's own ``__init__`` keeps them stable.
"""

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
    NotificationKind,
    OutputFormat,
    ResourceEncoding,
    TaskPolicy,
    TaskStatus,
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
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.subscriptions.types.subscription_filter import SubscriptionFilter
from rest_framework_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.tasks.types.task_executor import TaskExecutor
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from rest_framework_mcp.tasks.types.task_store import TaskStore
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
    "DRFPermissionAdapter",
    "DjangoCacheSessionStore",
    "DjangoPermRequired",
    "InMemorySSEBroker",
    "InMemorySSEReplayBuffer",
    "Icon",
    "IconTheme",
    "InMemorySessionStore",
    "JsonRpcError",
    "JsonRpcErrorCode",
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
    "SSEBroker",
    "SSEReplayBuffer",
    "ScopeRequired",
    "SelectorDefaults",
    "ServiceDefaults",
    "SessionStore",
    "DjangoCacheTaskStore",
    "InMemoryTaskStore",
    "TaskExecutor",
    "TaskPolicy",
    "TaskRecord",
    "TaskStatus",
    "TaskStore",
    "InMemorySubscriptionBroker",
    "NotificationKind",
    "SubscriptionBroker",
    "SubscriptionFilter",
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
    "QueryParam",
    "UrlKwarg",
    "register_tools",
    "__version__",
]
