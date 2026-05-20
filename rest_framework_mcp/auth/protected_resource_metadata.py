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
class ProtectedResourceMetadataViewSet(ViewSet):
    """RFC 9728 OAuth 2.0 Protected Resource Metadata endpoint.

    Mounted at ``/.well-known/oauth-protected-resource`` by :class:`MCPServer`.
    Single-action ViewSet — the canonical GET is wired as ``list`` via
    ``ProtectedResourceMetadataViewSet.as_view({"get": "list"}, auth_backend=...)``
    so the URL conf doesn't need a router.

    Delegates payload construction to the configured
    :meth:`MCPAuthBackend.protected_resource_metadata`, which returns a
    :class:`ProtectedResourceMetadata` dataclass. The backend is
    instance-scoped — passed in via ``as_view`` — so multiple servers in
    one process can advertise different metadata.

    DRF's default authentication / permission / throttling layers are
    disabled (empty lists below): PRM is a public discovery endpoint by
    design and the MCP transport owns its own auth pipeline through
    :class:`MCPAuthBackend`. The renderer is pinned to JSON because the
    payload shape is RFC-defined; content negotiation would be noise.
    """

    authentication_classes: tuple = ()  # noqa: RUF012 — DRF class-level config
    permission_classes = (AllowAny,)
    renderer_classes = (JSONRenderer,)

    auth_backend: MCPAuthBackend | None = None

    def list(self, request: Request) -> Response:  # noqa: ARG002 — DRF action signature
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("ProtectedResourceMetadataViewSet is missing an MCPAuthBackend")
        return Response(self.auth_backend.protected_resource_metadata().to_dict())


__all__ = ["ProtectedResourceMetadataViewSet"]
