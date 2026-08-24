"""WSGI entry point for the invoicing MCP example.

What ``manage.py runserver`` loads, and what ``gunicorn
invoicing.wsgi:application`` would serve. The sync ``server.urls`` this
example mounts runs fine here; ``asgi.py`` is the entry point to use when a
project switches to ``server.async_urls``.
"""

from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "invoicing.settings")
application = get_wsgi_application()
