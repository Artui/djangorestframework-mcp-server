"""URL conf — mounts the MCP server at ``/mcp/``."""

from __future__ import annotations

from django.urls import path
from invoices.mcp import build_server

server = build_server()

urlpatterns = [
    # ``server.urls`` is the namespaced ``(patterns, app_name, namespace)``
    # triple ``path()`` mounts directly, the ``admin.site.urls`` idiom — it is
    # not an argument for ``include()``.
    path("mcp/", server.urls),
]
