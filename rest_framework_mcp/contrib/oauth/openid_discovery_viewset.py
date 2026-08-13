from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.contrib.oauth.types.openid_discovery_payload import OpenIDDiscoveryPayload
from rest_framework_mcp.contrib.oauth.utils import supported_id_token_algorithms


@method_decorator(never_cache, name="dispatch")
class OpenIDDiscoveryViewSet(ViewSet):
    """OIDC discovery alias — ``/.well-known/openid-configuration``.

    Some MCP hosts probe this path before falling back to RFC 8414, so the
    payload is the backend's AS metadata plus a few OIDC defaults, letting the
    probe succeed even though this package implements no ID-token endpoint.
    Wired as the ``list`` action:
    ``as_view({"get": "list"}, auth_backend=...)``.

    The additions are ``subject_types_supported: ["public"]``,
    ``response_modes_supported: ["query"]``, and
    ``id_token_signing_alg_values_supported`` — the last **derived** rather
    than fixed, because wherever DOT is the authorization server with
    ``OIDC_ENABLED`` its token endpoint really does mint ID tokens. A client
    that read a hardcoded ``RS256`` and requested ``openid`` would reach
    ``Application.jwk_key`` on a client registered with no algorithm and take
    an ``ImproperlyConfigured`` 500, after logging in and consenting. The list
    is empty when no RSA key is configured.

    A backend that hosts no authorization server raises
    ``NotImplementedError``, surfaced as ``501`` for parity with
    [`AuthorizationServerMetadataViewSet`][rest_framework_mcp.contrib.oauth.authorization_server_metadata_viewset.AuthorizationServerMetadataViewSet].
    """

    authentication_classes: tuple = ()  # noqa: RUF012 — DRF class-level config
    permission_classes = (AllowAny,)
    renderer_classes = (JSONRenderer,)

    auth_backend: MCPAuthBackend | None = None

    def list(self, request: Request) -> Response:  # noqa: ARG002 — DRF action signature
        if self.auth_backend is None:  # pragma: no cover - guarded by build_oauth_urlpatterns
            raise RuntimeError("OpenIDDiscoveryViewSet is missing an MCPAuthBackend")
        try:
            base = self.auth_backend.authorization_server_metadata()
        except NotImplementedError as exc:
            return Response(
                {"error": "authorization_server_unavailable", "error_description": str(exc)},
                status=501,
            )
        payload = OpenIDDiscoveryPayload(
            base=base,
            id_token_signing_alg_values_supported=supported_id_token_algorithms(
                rsa_key_configured=self._rsa_key_configured()
            ),
        )
        return Response(payload.to_dict())

    @staticmethod
    def _rsa_key_configured() -> bool:
        """Whether DOT holds an RSA signing key.

        Lazy and tolerant of DOT's absence: this ViewSet renders whatever
        ``MCPAuthBackend`` returns, while the only signing key it can see
        belongs to DOT. A mount fronting some other authorization server
        reports no signable algorithm rather than guessing for it.
        """
        try:
            from oauth2_provider.settings import (
                oauth2_settings,
            )
        except ImportError:  # pragma: no cover - exercised by smoke job w/o DOT
            return False
        return bool(oauth2_settings.OIDC_RSA_PRIVATE_KEY)


__all__ = ["OpenIDDiscoveryViewSet"]
