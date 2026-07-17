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
        path("mcp/", server.urls),
        *build_oauth_urlpatterns(server=server, include_dcr=True),
    ]
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
        DCR is gated by ``dcr_enabled`` anyway and consumers who don't
        need it shouldn't even see the URL.
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
      auth_user_adapter: An :class:`AuthUserAdapter` instance that hydrates
        ``request.user`` before DOT's ``AuthorizationView`` dispatches.
        ``None`` (the default) means "no adapter; DOT's own dispatch
        decides the user" — typically a session-based login redirect.
        Only used when ``include_authorize`` is on.
      dcr_enabled: Whether ``/oauth/register/`` accepts registrations.
        ``None`` takes ``REST_FRAMEWORK_MCP['DCR_ENABLED']`` (default
        ``False`` — an open DCR endpoint lets anyone create an OAuth
        client against your authorization server).
      dcr_initial_access_token: RFC 7591 §3 token a DCR client must
        present. ``None`` takes ``REST_FRAMEWORK_MCP
        ['DCR_INITIAL_ACCESS_TOKEN']``, whose own default is ``None`` —
        meaning no token check.

    Every value is resolved **here**, when the patterns are built, rather than
    on each request — the same reason ``server`` is a parameter: so two mounts
    in one project can differ.
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
