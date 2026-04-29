"""Minimal Django settings for the invoicing MCP example.

SQLite + locmem cache + no authentication. Drop in production-grade
collaborators (Postgres, Redis, ``DjangoOAuthToolkitBackend``) when
adapting to your stack.
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR: Path = Path(__file__).resolve().parent.parent

# Don't ship this in production.
SECRET_KEY: str = "example-only-do-not-use-in-production"
DEBUG: bool = True
ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS: list[str] = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "invoices.apps.InvoicesConfig",
]

MIDDLEWARE: list[str] = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF: str = "invoicing.urls"
WSGI_APPLICATION: str = "invoicing.wsgi.application"
ASGI_APPLICATION: str = "invoicing.asgi.application"

DATABASES: dict[str, dict[str, str]] = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(BASE_DIR / "db.sqlite3"),
    }
}

CACHES: dict[str, dict[str, str]] = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

USE_TZ: bool = True
DEFAULT_AUTO_FIELD: str = "django.db.models.BigAutoField"

# MCP-specific configuration.
REST_FRAMEWORK_MCP: dict[str, object] = {
    # Allow any origin in dev. Production deployments must lock this down.
    "ALLOWED_ORIGINS": ["*"],
    # Use the dev-only auth backend. Swap for DOT in production.
    "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
    # In-memory session store for single-process dev. DjangoCacheSessionStore for multi-worker.
    "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
    "SERVER_INFO": {"name": "invoicing-example", "version": "0.0.1"},
}
