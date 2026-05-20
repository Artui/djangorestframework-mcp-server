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
    - ``id_token_signing_alg_values_supported: ["RS256"]`` — the most
      common OIDC algorithm, advertised even though we don't actually
      mint ID tokens (clients that walk this list and pick one work
      fine because they only use it for verification — which they
      never get to do without an ID-token endpoint anyway).
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
        payload = OpenIDDiscoveryPayload(base=base)
        return Response(payload.to_dict())


__all__ = ["OpenIDDiscoveryViewSet"]
