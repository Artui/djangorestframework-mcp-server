# Auth

Backends, permissions, response builders, and the RFC 9728 PRM view.

## Protocols

::: rest_framework_mcp.auth.auth_backend.MCPAuthBackend
::: rest_framework_mcp.auth.token_info.TokenInfo
::: rest_framework_mcp.auth.permissions.mcp_permission.MCPPermission

## Backends

::: rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend
::: rest_framework_mcp.auth.backends.django_oauth_toolkit_backend.DjangoOAuthToolkitBackend

## Permissions

::: rest_framework_mcp.auth.permissions.scope_required.ScopeRequired
::: rest_framework_mcp.auth.permissions.django_perm_required.DjangoPermRequired

## Rate limits

::: rest_framework_mcp.auth.rate_limits.mcp_rate_limit.MCPRateLimit
::: rest_framework_mcp.auth.rate_limits.fixed_window_rate_limit.FixedWindowRateLimit
::: rest_framework_mcp.auth.rate_limits.sliding_window_rate_limit.SlidingWindowRateLimit
::: rest_framework_mcp.auth.rate_limits.token_bucket_rate_limit.TokenBucketRateLimit

## Response helpers

::: rest_framework_mcp.auth.unauthenticated_response.build_unauthenticated_response
::: rest_framework_mcp.auth.insufficient_scope_response.build_insufficient_scope_response
::: rest_framework_mcp.auth.protected_resource_metadata.ProtectedResourceMetadataView
