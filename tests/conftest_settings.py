from __future__ import annotations

SECRET_KEY = "test"
DEBUG = False
ALLOWED_HOSTS = ["*"]
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "oauth2_provider",
    "tests.testapp",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

MIDDLEWARE: list[str] = []

ROOT_URLCONF = "tests.testapp.urls"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# The auth backend and session store are collaborators, passed to MCPServer by
# whoever builds it (see tests/testapp/mcp.py) rather than named here by dotted
# path. What remains are scalars.
REST_FRAMEWORK_MCP = {
    "ALLOWED_ORIGINS": ["*"],
    "SERVER_INFO": {"name": "djangorestframework-mcp-server-tests", "version": "0.0.0-test"},
}
