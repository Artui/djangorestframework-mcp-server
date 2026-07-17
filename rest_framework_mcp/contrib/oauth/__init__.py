from rest_framework_mcp.contrib.oauth.adapters.simplejwt_cookie import SimpleJWTCookieAdapter
from rest_framework_mcp.contrib.oauth.adapters.types.auth_user_adapter import AuthUserAdapter
from rest_framework_mcp.contrib.oauth.authorization_server_metadata_viewset import (
    AuthorizationServerMetadataViewSet,
)
from rest_framework_mcp.contrib.oauth.build_authorize_passthrough_view import (
    build_authorize_passthrough_view,
)
from rest_framework_mcp.contrib.oauth.build_oauth_urlpatterns import build_oauth_urlpatterns
from rest_framework_mcp.contrib.oauth.dcr_serializer import DynamicClientRegistrationSerializer
from rest_framework_mcp.contrib.oauth.dynamic_client_registration_viewset import (
    DynamicClientRegistrationViewSet,
)
from rest_framework_mcp.contrib.oauth.openid_discovery_viewset import OpenIDDiscoveryViewSet

__all__ = [
    "AuthUserAdapter",
    "AuthorizationServerMetadataViewSet",
    "DynamicClientRegistrationSerializer",
    "DynamicClientRegistrationViewSet",
    "OpenIDDiscoveryViewSet",
    "SimpleJWTCookieAdapter",
    "build_authorize_passthrough_view",
    "build_oauth_urlpatterns",
]
