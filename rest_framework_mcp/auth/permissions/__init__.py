from rest_framework_mcp.auth.permissions.django_perm_required import DjangoPermRequired
from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.auth.permissions.scope_required import ScopeRequired
from rest_framework_mcp.auth.permissions.types.mcp_permission import MCPPermission

__all__ = ["DRFPermissionAdapter", "DjangoPermRequired", "MCPPermission", "ScopeRequired"]
