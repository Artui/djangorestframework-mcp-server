"""URL conf — mounts the MCP server at ``/mcp/``."""

from __future__ import annotations

from django.urls import include, path
from invoices.mcp import build_server

server = build_server()

urlpatterns = [
    path("mcp/", include(server.urls)),
]
