"""URL conf — mounts MCP at ``/mcp/``. Uses ``async_urls`` so SSE GET works."""

from __future__ import annotations

from django.urls import include, path
from jobs.mcp import build_server

server = build_server()

urlpatterns = [
    path("mcp/", include(server.async_urls)),
]
