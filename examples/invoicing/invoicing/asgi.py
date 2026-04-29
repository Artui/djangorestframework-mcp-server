"""ASGI entry point for the invoicing MCP example.

Use ``uvicorn invoicing.asgi:application`` (or any ASGI server) when
mounting ``server.async_urls`` for non-blocking dispatch.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "invoicing.settings")
application = get_asgi_application()
