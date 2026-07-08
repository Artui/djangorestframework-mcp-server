from __future__ import annotations

from django.urls import path

from tests.testapp.mcp import build_server

server = build_server()

urlpatterns = [
    path("mcp/", server.urls),
]
