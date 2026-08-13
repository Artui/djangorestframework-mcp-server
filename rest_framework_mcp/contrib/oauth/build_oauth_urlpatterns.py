"""Opt-in OAuth endpoint matrix for MCP-friendly client compatibility.

MCP hosts probe several well-known URLs to find an authorization server: RFC 8414
mandates one path, RFC 9728 another, OIDC a third, and vendors mount their own aliases.
[`build_oauth_urlpatterns`][rest_framework_mcp.contrib.oauth.build_oauth_urlpatterns.build_oauth_urlpatterns]
serves every one of them. The aliases are *not* HTTP redirects — they render the same
payload from the same view, because redirects break clients that follow one and then
keep issuing requests against the redirected origin.

Mount the patterns alongside your ``MCPServer.urls``:

```python
urlpatterns = [
    path("mcp/", server.urls),
    *build_oauth_urlpatterns(server=server, include_dcr=True),
    # AFTER ours, not before — see below.
    path("oauth/", include("oauth2_provider.urls", namespace="oauth2_provider")),
]
```

**Order matters against ``oauth2_provider``, and getting it wrong is silent.**
DOT 3.4.0 serves its own ``register/`` and
``.well-known/oauth-authorization-server``, and Django resolves first-match, so
mounting DOT's urls first means DOT answers those paths — with an issuer of
``<host>/oauth`` rather than the site root. Nothing raises; clients read the
wrong metadata.
``check_oauth_url_shadowing`` detects it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import URLPattern, path

from rest_framework_mcp.auth.protected_resource_metadata import ProtectedResourceMetadataViewSet
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.contrib.oauth.adapters.types.auth_user_adapter import AuthUserAdapter
from rest_framework_mcp.contrib.oauth.authorization_server_metadata_viewset import (
    AuthorizationServerMetadataViewSet,
)
from rest_framework_mcp.contrib.oauth.build_authorize_passthrough_view import (
    build_authorize_passthrough_view,
)
from rest_framework_mcp.contrib.oauth.dynamic_client_registration_viewset import (
    DynamicClientRegistrationViewSet,
)
from rest_framework_mcp.contrib.oauth.openid_discovery_viewset import OpenIDDiscoveryViewSet

if TYPE_CHECKING:  # pragma: no cover - imported only for typing
    from rest_framework_mcp.server.mcp_server import MCPServer


def build_oauth_urlpatterns(
    *,
    server: MCPServer,
    include_dcr: bool = False,
    include_aliases: bool = True,
    include_openid_discovery: bool = True,
    include_authorize: bool = False,
    auth_user_adapter: AuthUserAdapter | None = None,
    dcr_enabled: bool | None = None,
    dcr_initial_access_token: str | None = None,
) -> list[URLPattern]:
    """Return URL patterns for the OAuth endpoint matrix.

    With every flag on, the canonical paths and their aliases are::

        /.well-known/oauth-protected-resource     ProtectedResourceMetadataViewSet
          + /.well-known/oauth-protected-resource/mcp
          + /mcp/.well-known/oauth-protected-resource
        /.well-known/oauth-authorization-server   AuthorizationServerMetadataViewSet
          + /.well-known/oauth-authorization-server/oauth
          + /oauth/.well-known/oauth-authorization-server
        /.well-known/openid-configuration         OpenIDDiscoveryViewSet
          + /.well-known/openid-configuration/oauth
        /oauth/register/                          DynamicClientRegistrationViewSet

    DOT's own ``/oauth/authorize/`` and ``/oauth/token/`` are **not** mounted:
    this covers the discovery and DCR surface, while the AS endpoints belong to
    whichever framework hosts the authorization server. Every argument is
    resolved here, when the patterns are built, rather than per request, so two
    mounts in one project can differ.

    Args:
      server: The [`MCPServer`][rest_framework_mcp.server.mcp_server.MCPServer]
        whose ``auth_backend`` drives every discovery payload. A parameter rather than a settings lookup so
        multi-server deployments work.
      include_dcr: Mount ``/oauth/register/``. Off by default, so a consumer
        who does not want DCR never exposes the URL at all.
      include_aliases: Mount the alias URLs alongside the canonical ones.
      include_openid_discovery: Mount the OIDC discovery alias.
      include_authorize: Mount ``/oauth/authorize/`` as a thin DOT
        ``AuthorizationView`` subclass carrying the
        ``auth_user_adapter`` hook. Off by default because the consumer's URL
        conf usually owns that path via ``include('oauth2_provider.urls')``;
        turn it on to wire the adapter when it does not. Requires the
        ``[oauth]`` extra.
      auth_user_adapter: Hydrates ``request.user`` before DOT's
        ``AuthorizationView`` dispatches. ``None`` leaves the user to DOT's own
        dispatch, typically a session-based login redirect. Only read when
        ``include_authorize`` is on.
      dcr_enabled: Whether ``/oauth/register/`` accepts registrations. ``None``
        takes ``REST_FRAMEWORK_MCP['DCR_ENABLED']``.
      dcr_initial_access_token: RFC 7591 §3 token a DCR client must present.
        ``None`` takes ``REST_FRAMEWORK_MCP['DCR_INITIAL_ACCESS_TOKEN']``,
        itself ``None``, which means no token check.
    """
    backend = server.auth_backend
    patterns: list[URLPattern] = [
        path(
            ".well-known/oauth-protected-resource",
            ProtectedResourceMetadataViewSet.as_view({"get": "list"}, auth_backend=backend),
            name="mcp-oauth-prm",
        ),
        path(
            ".well-known/oauth-authorization-server",
            AuthorizationServerMetadataViewSet.as_view({"get": "list"}, auth_backend=backend),
            name="mcp-oauth-as-metadata",
        ),
    ]

    if include_aliases:
        patterns.extend(
            [
                path(
                    ".well-known/oauth-protected-resource/mcp",
                    ProtectedResourceMetadataViewSet.as_view({"get": "list"}, auth_backend=backend),
                    name="mcp-oauth-prm-mcp-alias",
                ),
                path(
                    "mcp/.well-known/oauth-protected-resource",
                    ProtectedResourceMetadataViewSet.as_view({"get": "list"}, auth_backend=backend),
                    name="mcp-oauth-prm-local-alias",
                ),
                path(
                    ".well-known/oauth-authorization-server/oauth",
                    AuthorizationServerMetadataViewSet.as_view(
                        {"get": "list"}, auth_backend=backend
                    ),
                    name="mcp-oauth-as-metadata-oauth-alias",
                ),
                path(
                    "oauth/.well-known/oauth-authorization-server",
                    AuthorizationServerMetadataViewSet.as_view(
                        {"get": "list"}, auth_backend=backend
                    ),
                    name="mcp-oauth-as-metadata-local-alias",
                ),
            ]
        )

    if include_openid_discovery:
        patterns.append(
            path(
                ".well-known/openid-configuration",
                OpenIDDiscoveryViewSet.as_view({"get": "list"}, auth_backend=backend),
                name="mcp-oauth-oidc-discovery",
            )
        )
        if include_aliases:
            patterns.append(
                path(
                    ".well-known/openid-configuration/oauth",
                    OpenIDDiscoveryViewSet.as_view({"get": "list"}, auth_backend=backend),
                    name="mcp-oauth-oidc-discovery-alias",
                )
            )

    if include_dcr:
        patterns.append(
            path(
                "oauth/register/",
                DynamicClientRegistrationViewSet.as_view(
                    {"post": "create"},
                    dcr_enabled=(
                        dcr_enabled if dcr_enabled is not None else bool(get_setting("DCR_ENABLED"))
                    ),
                    initial_access_token=(
                        dcr_initial_access_token
                        if dcr_initial_access_token is not None
                        else get_setting("DCR_INITIAL_ACCESS_TOKEN")
                    ),
                ),
                name="mcp-oauth-dcr",
            )
        )

    if include_authorize:
        patterns.append(
            path(
                "oauth/authorize/",
                build_authorize_passthrough_view(auth_user_adapter),
                name="mcp-oauth-authorize",
            )
        )

    return patterns


__all__ = ["build_oauth_urlpatterns"]
