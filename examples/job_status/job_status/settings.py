"""Minimal Django settings for the job-status MCP example."""

from __future__ import annotations

from pathlib import Path

BASE_DIR: Path = Path(__file__).resolve().parent.parent

SECRET_KEY: str = "example-only-do-not-use-in-production"
DEBUG: bool = True
ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS: list[str] = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "jobs.apps.JobsConfig",
]

MIDDLEWARE: list[str] = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF: str = "job_status.urls"
WSGI_APPLICATION: str = "job_status.wsgi.application"
ASGI_APPLICATION: str = "job_status.asgi.application"

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

REST_FRAMEWORK_MCP: dict[str, object] = {
    "ALLOWED_ORIGINS": ["*"],
    "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
    "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
    "SERVER_INFO": {"name": "job-status-example", "version": "0.0.1"},
}
