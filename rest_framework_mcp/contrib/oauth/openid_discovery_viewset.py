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

    Some MCP / LLM-host clients probe ``/.well-known/openid-configuration``
    before falling back to RFC 8414. This ViewSet returns the same
    payload as :class:`AuthorizationServerMetadataViewSet` plus a small
    set of OIDC defaults so the probe succeeds even though
    :mod:`rest_framework_mcp` doesn't implement an actual ID-token
    endpoint.

    Single-action ViewSet — the canonical GET wires up as ``list`` via
    ``OpenIDDiscoveryViewSet.as_view({"get": "list"}, auth_backend=...)``.

    Concretely, the payload is the backend's AS metadata composed with:

    - ``subject_types_supported: ["public"]`` — DOT-style pseudonymous
      identifiers.
    - ``id_token_signing_alg_values_supported`` — **derived**, not fixed.
      This used to be a hardcoded ``["RS256"]``, on the reasoning that
      the value is inert because "we don't actually mint ID tokens". That
      reasoning was wrong wherever DOT *is* the authorization server with
      ``OIDC_ENABLED``: DOT's token endpoint mints ID tokens, so a client
      that read this list, saw RS256 and requested ``openid`` reached
      ``Application.jwk_key`` on a DCR-registered client that had no
      algorithm — ``ImproperlyConfigured``, surfacing as a 500 after the
      user had already logged in and consented. It now reports what the
      server can genuinely sign with, which is empty when no RSA key is
      configured.
    - ``response_modes_supported: ["query"]`` — standard.

    Backends that don't host an authorization server raise
    :class:`NotImplementedError`; this view surfaces that as ``501`` for
    parity with :class:`AuthorizationServerMetadataViewSet`.
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

        Lazy + tolerant of DOT's absence: this ViewSet is backend-agnostic
        (it renders whatever ``MCPAuthBackend`` returns), while the only
        signing key it can see belongs to DOT. A mount fronting some other
        authorization server reports no signable algorithm rather than
        guessing on that server's behalf.
        """
        try:
            from oauth2_provider.settings import (  # type: ignore[import-not-found]
                oauth2_settings,
            )
        except ImportError:  # pragma: no cover - exercised by smoke job w/o DOT
            return False
        return bool(oauth2_settings.OIDC_RSA_PRIVATE_KEY)


__all__ = ["OpenIDDiscoveryViewSet"]
