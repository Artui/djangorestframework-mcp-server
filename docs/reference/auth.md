# Auth

Backends, permissions, response builders, rate limits, and OAuth-related views
(RFC 9728 PRM + the opt-in contrib `oauth/` mount).

## Protocols

::: rest_framework_mcp.auth.types.auth_backend.MCPAuthBackend
::: rest_framework_mcp.auth.types.token_info.TokenInfo
::: rest_framework_mcp.auth.permissions.types.mcp_permission.MCPPermission

## Backends

::: rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend
::: rest_framework_mcp.auth.backends.django_oauth_toolkit_backend.DjangoOAuthToolkitBackend

## Permissions

::: rest_framework_mcp.auth.permissions.scope_required.ScopeRequired
::: rest_framework_mcp.auth.permissions.django_perm_required.DjangoPermRequired
::: rest_framework_mcp.auth.permissions.drf_permission_adapter.DRFPermissionAdapter

## Rate limits

::: rest_framework_mcp.auth.rate_limits.types.mcp_rate_limit.MCPRateLimit
::: rest_framework_mcp.auth.rate_limits.fixed_window_rate_limit.FixedWindowRateLimit
::: rest_framework_mcp.auth.rate_limits.sliding_window_rate_limit.SlidingWindowRateLimit
::: rest_framework_mcp.auth.rate_limits.token_bucket_rate_limit.TokenBucketRateLimit

## Response helpers

::: rest_framework_mcp.auth.unauthenticated_response.build_unauthenticated_response
::: rest_framework_mcp.auth.insufficient_scope_response.build_insufficient_scope_response

## Protected Resource Metadata (RFC 9728)

::: rest_framework_mcp.auth.protected_resource_metadata.ProtectedResourceMetadataViewSet
::: rest_framework_mcp.auth.types.protected_resource_metadata.ProtectedResourceMetadata

## OAuth contrib (opt-in)

`build_oauth_urlpatterns(server, *, include_dcr=False, include_aliases=True,
include_openid_discovery=True)` returns a list of URL patterns ready to mount
alongside your `MCPServer.urls`. Exposes RFC 8414 / OIDC discovery /
RFC 7591 Dynamic Client Registration + the alias paths different LLM hosts
probe (aliases render the canonical payload — they are not HTTP redirects).

::: rest_framework_mcp.contrib.oauth.build_oauth_urlpatterns.build_oauth_urlpatterns
::: rest_framework_mcp.contrib.oauth.authorization_server_metadata_viewset.AuthorizationServerMetadataViewSet
::: rest_framework_mcp.contrib.oauth.openid_discovery_viewset.OpenIDDiscoveryViewSet
::: rest_framework_mcp.contrib.oauth.dynamic_client_registration_viewset.DynamicClientRegistrationViewSet
::: rest_framework_mcp.contrib.oauth.dcr_serializer.DynamicClientRegistrationSerializer
::: rest_framework_mcp.auth.types.authorization_server_metadata.AuthorizationServerMetadata
::: rest_framework_mcp.contrib.oauth.types.openid_discovery_payload.OpenIDDiscoveryPayload
::: rest_framework_mcp.contrib.oauth.types.dynamic_client_registration_request.DynamicClientRegistrationRequest
::: rest_framework_mcp.contrib.oauth.types.dynamic_client_registration_response.DynamicClientRegistrationResponse

DCR is gated behind two settings:

- `REST_FRAMEWORK_MCP["DCR_ENABLED"]` (default `False`) — DCR endpoint
  returns `501 Not Implemented` while disabled.
- `REST_FRAMEWORK_MCP["DCR_INITIAL_ACCESS_TOKEN"]` (default `None`) —
  optional bearer required on the DCR POST, per RFC 7591 §3.

## User-adapter hook (cookie-session bridge for `/authorize`)

::: rest_framework_mcp.contrib.oauth.adapters.types.auth_user_adapter.AuthUserAdapter
::: rest_framework_mcp.contrib.oauth.adapters.simplejwt_cookie.SimpleJWTCookieAdapter
