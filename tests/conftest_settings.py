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

# Only ``register_ui_resource(template_name=...)`` needs an engine — the rest of
# the package renders nothing. Kept minimal and app-dirs-only so the UI-view
# tests load `tests/testapp/templates/`.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {},
    }
]

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
    # ⚠ The suite opts *out* of the strict default this package now ships.
    #
    # 0.25.0 flipped REQUIRE_TOOL_PERMISSIONS to True, so registering a tool
    # with no permissions raises. Roughly 260 fixtures across ~50 files here
    # register tools whose subject is something else entirely — output
    # encoding, pagination, task lifecycle — and guarding every one of them
    # would add noise to 260 call sites to test nothing.
    #
    # The flip's own behaviour is covered directly in
    # tests/test_consumer_ergonomics.py, including an assertion that the
    # *shipped* default is True, so this override cannot mask a regression
    # back to permissive.
    #
    # It is also an honest measure of the upgrade cost: if this package's own
    # suite needs the opt-out, a consumer's may too — which is exactly why the
    # setting exists rather than the check being unconditional.
    "REQUIRE_TOOL_PERMISSIONS": False,
    "SERVER_INFO": {"name": "djangorestframework-mcp-server-tests", "version": "0.0.0-test"},
}
