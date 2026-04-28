from rest_framework_mcp.auth.permissions.django_perm_required import DjangoPermRequired
from rest_framework_mcp.auth.permissions.mcp_permission import MCPPermission
from rest_framework_mcp.auth.permissions.scope_required import ScopeRequired

__all__ = ["DjangoPermRequired", "MCPPermission", "ScopeRequired"]
