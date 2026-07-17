from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings

DEFAULTS: dict[str, Any] = {
    "PROTOCOL_VERSIONS": ["2025-11-25", "2025-06-18"],
    # When True (the spec-compliant default), non-``initialize`` requests must
    # carry an ``MCP-Protocol-Version`` header naming a supported version, or
    # they are rejected with HTTP 400. Some real-world clients omit the header
    # entirely; set this to False to accept those requests by falling back to
    # the first entry in ``PROTOCOL_VERSIONS``. A *present-but-unsupported*
    # header is still rejected either way — silently downgrading there would
    # mask a genuine version mismatch.
    "REQUIRE_PROTOCOL_VERSION_HEADER": True,
    # When True (default), successful ``tools/call`` results include a
    # ``structuredContent`` field carrying the typed JSON payload alongside
    # the human-readable ``content[0]`` text. Set to False to omit
    # ``structuredContent`` server-wide — useful when a downstream client
    # echoes both fields back to the LLM and burns context, or chokes on the
    # field altogether. Individual tools can override either direction via
    # the ``include_structured_content`` kwarg on registration.
    #
    # ``INCLUDE_STRUCTURED_CONTENT`` and ``INCLUDE_OUTPUT_SCHEMA`` are
    # independent settings, but the MCP spec forbids one combination:
    # advertising ``outputSchema`` without emitting ``structuredContent``.
    # If you turn ``INCLUDE_STRUCTURED_CONTENT`` off, also set
    # ``INCLUDE_OUTPUT_SCHEMA`` to False (or set a per-binding
    # ``include_output_schema=False``) — otherwise the resolver raises
    # ``ImproperlyConfigured`` at request time.
    "INCLUDE_STRUCTURED_CONTENT": True,
    # When True (default), tool descriptors in ``tools/list`` carry an
    # ``outputSchema`` built from the binding's output serializer. Set to
    # False to suppress the schema announcement server-wide while still
    # allowing ``structuredContent`` to flow on the response (the spec
    # allows that direction, see SEP-1624). Individual tools can override
    # via the ``include_output_schema`` kwarg on registration.
    #
    # The reverse combo — ``outputSchema`` advertised but
    # ``structuredContent`` suppressed — is a spec violation and is
    # rejected with ``ImproperlyConfigured`` at request time.
    "INCLUDE_OUTPUT_SCHEMA": True,
    "ALLOWED_ORIGINS": [],
    "DEFAULT_OUTPUT_FORMAT": "json",
    "SERVER_INFO": {"name": "djangorestframework-mcp-server"},
    "MAX_REQUEST_BYTES": 1_048_576,
    # Default canonical resource URL, used for RFC 8707 audience enforcement by
    # token-validating auth backends. ``None`` disables enforcement (suitable
    # for development / behind a separate gateway). When set, tokens whose
    # ``aud`` / ``resource`` claim does not match are rejected.
    #
    # This is only the **default** for ``MCPServer(resource_url=...)``. RFC 8707
    # binds a token to *a* resource, so each server in a project needs its own
    # canonical URL — two servers sharing one URL means a token minted for one
    # passes the audience check at the other, which is the exact replay this
    # mechanism exists to prevent. Set it per server; leave this for the
    # single-server case.
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
    # Dynamic Client Registration (RFC 7591) gate. ``False`` (default) means
    # the contrib ``/oauth/register/`` endpoint refuses every request with
    # 403. Turn on only when you've thought through the abuse surface — an
    # open DCR endpoint lets anyone create an OAuth client against your
    # authorization server.
    "DCR_ENABLED": False,
    # Optional initial-access-token (RFC 7591 §3) that DCR clients must
    # present in ``Authorization: Bearer <token>`` to register. ``None``
    # means "no token check" — equivalent to "anyone who can reach the
    # endpoint can register". Setting a static token is the simplest way
    # to gate DCR behind shared knowledge; rotate it manually when needed.
    "DCR_INITIAL_ACCESS_TOKEN": None,
    # Dotted path to a :class:`AuthUserAdapter` implementation that hydrates
    # ``request.user`` before DOT's ``AuthorizationView`` dispatches. ``None``
    # (the default) means "no hydration; rely on Django's session middleware
    # to populate ``request.user``". Used by the contrib ``include_authorize``
    # passthrough wired by :func:`build_oauth_urlpatterns`.
    "AUTH_USER_ADAPTER": None,
    # Name of the cookie the SimpleJWT adapter reads access tokens from.
    # Defaults to ``"access"`` which matches ``djangorestframework-simplejwt``'s
    # documented ``AUTH_COOKIE`` default.
    "SIMPLEJWT_ACCESS_COOKIE": "access",
    # When True, ``tools/list`` / ``resources/list`` /
    # ``resources/templates/list`` / ``prompts/list`` filter out bindings
    # whose ``permissions`` deny the current caller. Off by default
    # (existing wire shape unchanged). Per-binding ``always_listed=True``
    # opts a binding back into the listing even when the caller can't
    # invoke it — useful as a discovery aid for admin tools etc.
    "FILTER_LISTINGS_BY_PERMISSIONS": False,
    # When True, registering a tool with no permissions at all (neither
    # ``spec.permission_classes`` nor a per-binding ``permissions=[...]``)
    # raises ``ImproperlyConfigured`` instead of emitting the default
    # ``UnguardedToolWarning``. The warning exists because the most common
    # DRF habit — guarding the *viewset* (or relying on the
    # ``REST_FRAMEWORK`` default permission classes) — has no effect over
    # MCP: this package deliberately bypasses DRF's view-layer pipeline,
    # so a spec that looks guarded over HTTP ships as an unguarded tool.
    "REQUIRE_TOOL_PERMISSIONS": False,
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
