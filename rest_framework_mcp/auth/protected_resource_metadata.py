from __future__ import annotations

from typing import Any

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from rest_framework_mcp.auth.auth_backend import MCPAuthBackend


@method_decorator(never_cache, name="dispatch")
class ProtectedResourceMetadataView(View):
    """RFC 9728 OAuth 2.0 Protected Resource Metadata endpoint.

    Mounted at ``/.well-known/oauth-protected-resource`` by :class:`MCPServer`.
    Delegates payload construction to the configured
    :class:`MCPAuthBackend.protected_resource_metadata`. The backend is
    instance-scoped — passed in via ``as_view`` — so multiple servers in one
    process can advertise different metadata.
    """

    http_method_names = ["get", "head", "options"]

    auth_backend: MCPAuthBackend | None = None

    def get(self, request: HttpRequest) -> JsonResponse:
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("ProtectedResourceMetadataView is missing an MCPAuthBackend")
        metadata: dict[str, Any] = self.auth_backend.protected_resource_metadata()
        return JsonResponse(metadata)


__all__ = ["ProtectedResourceMetadataView"]
