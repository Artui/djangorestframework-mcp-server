from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend


@method_decorator(never_cache, name="dispatch")
class AuthorizationServerMetadataViewSet(ViewSet):
    """RFC 8414 OAuth 2.0 Authorization Server Metadata endpoint.

    Mounted by :func:`build_oauth_urlpatterns` at the canonical
    ``/.well-known/oauth-authorization-server`` URL plus aliases. The
    canonical GET wires up as the ``list`` action via
    ``AuthorizationServerMetadataViewSet.as_view({"get": "list"}, auth_backend=...)``.

    Delegates payload construction to
    :meth:`MCPAuthBackend.authorization_server_metadata`, which returns
    an :class:`AuthorizationServerMetadata` dataclass. Backends that
    don't host an authorization server raise :class:`NotImplementedError`
    on the method; this view surfaces that as ``501 Not Implemented`` so
    clients see a deterministic "no AS here" signal rather than a 500.

    DRF's default auth / permission / throttling layers are disabled —
    discovery is public and the MCP transport owns its own pipeline.
    The renderer is pinned to JSON; the payload shape is RFC-defined.
    """

    authentication_classes: tuple = ()  # noqa: RUF012 — DRF class-level config
    permission_classes = (AllowAny,)
    renderer_classes = (JSONRenderer,)

    auth_backend: MCPAuthBackend | None = None

    def list(self, request: Request) -> Response:  # noqa: ARG002 — DRF action signature
        if self.auth_backend is None:  # pragma: no cover - guarded by build_oauth_urlpatterns
            raise RuntimeError("AuthorizationServerMetadataViewSet is missing an MCPAuthBackend")
        try:
            metadata = self.auth_backend.authorization_server_metadata()
        except NotImplementedError as exc:
            return Response(
                {"error": "authorization_server_unavailable", "error_description": str(exc)},
                status=501,
            )
        return Response(metadata.to_dict())


__all__ = ["AuthorizationServerMetadataViewSet"]
