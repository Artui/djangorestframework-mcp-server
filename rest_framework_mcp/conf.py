from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings

DEFAULTS: dict[str, Any] = {
    "PROTOCOL_VERSIONS": ["2025-11-25", "2025-06-18"],
    "AUTH_BACKEND": (
        "rest_framework_mcp.auth.backends.django_oauth_toolkit_backend.DjangoOAuthToolkitBackend"
    ),
    "SESSION_STORE": (
        "rest_framework_mcp.transport.django_cache_session_store.DjangoCacheSessionStore"
    ),
    "ALLOWED_ORIGINS": [],
    "DEFAULT_OUTPUT_FORMAT": "json",
    "SERVER_INFO": {"name": "djangorestframework-mcp-server"},
    "MAX_REQUEST_BYTES": 1_048_576,
    # The canonical resource URL of this MCP server, used for RFC 8707 audience
    # enforcement by token-validating auth backends. ``None`` disables
    # enforcement (suitable for development / behind a separate gateway). When
    # set, tokens whose ``aud`` / ``resource`` claim does not match are rejected.
    "RESOURCE_URL": None,
    # Maximum number of items returned by a single list-style call
    # (``tools/list``, ``resources/list``, ``resources/templates/list``,
    # ``prompts/list``). Clients page through using the opaque ``cursor`` token
    # echoed in the response.
    "PAGE_SIZE": 100,
    # When True, validation-error JSON-RPC responses include the offending
    # ``arguments`` dict under ``data.value`` for client-side debugging. Off
    # by default because the dict can carry sensitive payloads (PII, secrets)
    # that consumers don't want flowing back to the client or appearing in
    # client-side logs.
    "INCLUDE_VALIDATION_VALUE": False,
    # When True, ``ServiceError`` raised from a tool callable is recorded on
    # the active OpenTelemetry span via ``record_exception`` before the
    # handler maps it to a JSON-RPC error. Off by default because every
    # error then flows into trace/error pipelines as an exception, which can
    # be noisy if your services raise ``ServiceError`` for routine
    # business-rule denials. Enable it when you treat ``ServiceError`` as a
    # genuine failure worth alerting on.
    # ``ServiceValidationError`` is never recorded — it represents
    # client-side input failure, not a server fault.
    "RECORD_SERVICE_EXCEPTIONS": False,
}


def get_setting(name: str) -> Any:
    """Return a single setting from ``REST_FRAMEWORK_MCP``, falling back to ``DEFAULTS``.

    Raises ``KeyError`` for unknown setting names so typos surface immediately.
    """
    if name not in DEFAULTS:
        raise KeyError(f"Unknown REST_FRAMEWORK_MCP setting: {name!r}")
    user_settings: dict[str, Any] = getattr(django_settings, "REST_FRAMEWORK_MCP", {}) or {}
    if name in user_settings:
        return user_settings[name]
    return DEFAULTS[name]
