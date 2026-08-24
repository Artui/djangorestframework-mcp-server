"""URL conf — mounts MCP at ``/mcp/``. Uses ``async_urls`` so SSE GET works."""

from __future__ import annotations

from django.urls import path
from jobs.mcp import build_server

server = build_server()

urlpatterns = [
    # The namespaced triple mounts directly; see the invoicing example.
    path("mcp/", server.async_urls),
]
