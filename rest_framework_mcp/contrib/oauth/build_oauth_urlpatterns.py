"""Opt-in OAuth endpoint matrix for MCP-friendly client compatibility.

MCP / LLM-host clients in the wild probe several different well-known
URLs to find an authorization server — RFC 8414 mandates one path, RFC
9728 another, and OIDC adds a third, while several vendors mount their
own alias paths. :func:`build_oauth_urlpatterns` produces a Django URL
pattern list that serves every alias the user is likely to need.

Aliases are *not* HTTP redirects — they render the same payload from
the same view. Redirects break clients that follow them once but then
keep issuing requests against the redirected origin.

Mount the patterns alongside your ``MCPServer.urls`` to expose a
contiguous set of endpoints:

.. code-block:: python

    urlpatterns = [
        *server.urls,
        *build_oauth_urlpatterns(server=server, include_dcr=True),
    ]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import URLPattern, path

from rest_framework_mcp.auth.protected_resource_metadata import ProtectedResourceMetadataViewSet
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
from rest_framework_mcp.contrib.oauth.resolve_auth_user_adapter import resolve_auth_user_adapter

if TYPE_CHECKING:  # pragma: no cover - imported only for typing
    from rest_framework_mcp.server.mcp_server import MCPServer


def build_oauth_urlpatterns(
    *,
    server: MCPServer,
    include_dcr: bool = False,
    include_aliases: bool = True,
    include_openid_discovery: bool = True,
    include_authorize: bool = False,
) -> list[URLPattern]:
    """Return URL patterns for the OAuth endpoint matrix.

    Endpoint matrix when all flags are ``True``:

    +---------------------------------------------------------+--------------------------------------+
    | Path                                                    | View                                 |
    +=========================================================+======================================+
    | ``/.well-known/oauth-protected-resource``               | ``ProtectedResourceMetadataViewSet``    |
    +---------------------------------------------------------+--------------------------------------+
    | ``/.well-known/oauth-protected-resource/mcp``           | alias                                |
    +---------------------------------------------------------+--------------------------------------+
    | ``/mcp/.well-known/oauth-protected-resource``           | alias                                |
    +---------------------------------------------------------+--------------------------------------+
    | ``/.well-known/oauth-authorization-server``             | ``AuthorizationServerMetadataViewSet``  |
    +---------------------------------------------------------+--------------------------------------+
    | ``/.well-known/oauth-authorization-server/oauth``       | alias                                |
    +---------------------------------------------------------+--------------------------------------+
    | ``/oauth/.well-known/oauth-authorization-server``       | alias                                |
    +---------------------------------------------------------+--------------------------------------+
    | ``/.well-known/openid-configuration``                   | ``OpenIDDiscoveryViewSet``              |
    +---------------------------------------------------------+--------------------------------------+
    | ``/.well-known/openid-configuration/oauth``             | alias                                |
    +---------------------------------------------------------+--------------------------------------+
    | ``/oauth/register/``                                    | ``DynamicClientRegistrationViewSet``    |
    +---------------------------------------------------------+--------------------------------------+

    The DOT-provided ``/oauth/authorize/`` and ``/oauth/token/`` views
    are NOT mounted here — consumers include ``oauth2_provider.urls``
    separately. The contrib mount focuses on the discovery / DCR
    surface; the AS endpoints themselves belong to whichever framework
    actually hosts the AS.

    Args:
      server: The :class:`MCPServer` whose ``auth_backend`` should drive
        all of the discovery payloads. Passed in instead of looked up
        from settings so multi-server deployments work.
      include_dcr: Mount ``/oauth/register/``. Default ``False`` because
        DCR is gated behind ``REST_FRAMEWORK_MCP['DCR_ENABLED']`` anyway
        and consumers who don't need it shouldn't even see the URL.
      include_aliases: Mount the alias URLs alongside the canonical ones.
        Default ``True`` because clients in the wild use varied paths.
      include_openid_discovery: Mount the OIDC discovery alias. Default
        ``True`` for the same reason.
      include_authorize: Mount ``/oauth/authorize/`` as a thin DOT
        :class:`AuthorizationView` subclass with the configured
        :class:`AuthUserAdapter` hook. Default ``False`` because the
        consumer's own URL conf typically owns ``/oauth/authorize/`` via
        ``include('oauth2_provider.urls')``; flip this on when you want
        the adapter wired and you're not including DOT's urls otherwise.
        Requires the ``[oauth]`` extra — DOT is imported lazily inside
        the factory.
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
                DynamicClientRegistrationViewSet.as_view({"post": "create"}),
                name="mcp-oauth-dcr",
            )
        )

    if include_authorize:
        adapter = resolve_auth_user_adapter()
        patterns.append(
            path(
                "oauth/authorize/",
                build_authorize_passthrough_view(adapter),
                name="mcp-oauth-authorize",
            )
        )

    return patterns


__all__ = ["build_oauth_urlpatterns"]
