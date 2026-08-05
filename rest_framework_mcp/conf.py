from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings

DEFAULTS: dict[str, Any] = {
    # Every revision this server speaks, most-preferred first, across both
    # eras. ``2026-07-28`` is *modern* (per-request metadata, no session);
    # the rest are *legacy* (``initialize`` handshake). One list, because a
    # version belongs to exactly one era — see ``MODERN_PROTOCOL_VERSIONS``.
    "PROTOCOL_VERSIONS": ["2026-07-28", "2025-11-25", "2025-06-18"],
    # When True (the spec-compliant default), non-``initialize`` requests must
    # carry an ``MCP-Protocol-Version`` header naming a supported version, or
    # they are rejected with HTTP 400. Some real-world clients omit the header
    # entirely; set this to False to accept those requests by falling back to
    # the first entry in ``PROTOCOL_VERSIONS``. A *present-but-unsupported*
    # header is still rejected either way — silently downgrading there would
    # mask a genuine version mismatch.
    "REQUIRE_PROTOCOL_VERSION_HEADER": True,
    # When False, the legacy (``initialize``-handshake) era runs **without
    # sessions**: no ``Mcp-Session-Id`` is minted at ``initialize``, none is
    # required on subsequent requests, and the SSE ``GET`` / session ``DELETE``
    # answer ``405`` (the status the spec defines for "this endpoint offers no
    # SSE stream" and "this server does not let clients terminate sessions").
    #
    # ⚠ This is a **conformant mode, not a relaxation.** Both legacy revisions
    # make assignment optional — "A server using the Streamable HTTP transport
    # MAY assign a session ID at initialization time" — and make the client's
    # obligation conditional on the server having assigned one. A server that
    # never assigns is never sent one.
    #
    # Why you would: a session is state, and state expires, gets evicted, and
    # dies with a deploy. Every such failure reaches the client as a ``404``
    # whose documented remedy is to re-``initialize`` — which not every client
    # does, turning a recoverable condition into an outage that needs a human.
    # Turning sessions off removes the failure class outright, for every client,
    # without waiting on one to implement the remedy. The **modern**
    # (``2026-07-28``) era is already sessionless and is unaffected by this
    # setting; this exists for deployments still serving legacy clients.
    #
    # What you give up: server-initiated messaging on the legacy era. The
    # session id is what addresses a client's SSE channel, so with it gone the
    # ``GET`` stream has no address and answers ``405``. Request/response tool
    # calling is untouched.
    "SESSIONS_ENABLED": True,
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
    # How long a client may cache a catalog result (``server/discover`` plus
    # the four list methods), in milliseconds. ``0`` = immediately stale.
    #
    # A catalog is fixed once the process boots, so the honest answer is "until
    # the next deploy" — which nothing here can know. One minute costs a client
    # a stale minute after a release rather than a stale catalog for the life of
    # its connection.
    "CATALOG_CACHE_TTL_MS": 60_000,
    # The same, for ``resources/read``. ``0`` by default because a resource body
    # is whatever a selector just produced — caching live data by default would
    # serve yesterday's invoice. Static resources opt in per binding with
    # ``cache_ttl_ms=``.
    "RESOURCE_CACHE_TTL_MS": 0,
    # How long a task stays readable, reported to the client as ``ttlMs``.
    # 24 hours: long enough that a queue backlog, a retry or an overnight job
    # does not turn into "unknown task" for a client that is still politely
    # polling, which is the failure that looks like work simply vanishing.
    # ``None`` disables expiry — see ``MCPConfig.task_ttl_ms`` for the caveat.
    "TASK_TTL_MS": 86_400_000,
    # Suggested ``tasks/get`` cadence, sent as ``pollIntervalMs``. Advisory;
    # clients SHOULD honour it. Five seconds suits work measured in tens of
    # seconds to minutes, which is what a task is for — tune it to the job
    # rather than leaving it at a default that fits neither end.
    "TASK_POLL_INTERVAL_MS": 5_000,
    # How long one ``subscriptions/listen`` stream may stay open before the
    # server closes it gracefully and the client re-subscribes.
    #
    # ⚠ This is an authorization control as much as a resource one. A
    # subscription's permissions are checked once, when it opens, so without a
    # cap a principal whose access was revoked keeps receiving change signals
    # for as long as it holds the connection. Re-subscription re-runs the check.
    # It also bounds how long one client can occupy a worker. ``None`` disables
    # the cap, which means both of those bounds go with it.
    "SUBSCRIPTION_MAX_SECONDS": 3600,
    # Ceiling on concurrent ``subscriptions/listen`` streams **per worker**.
    #
    # Each open subscription parks an ASGI task for its lifetime, so without a
    # bound an authenticated caller can exhaust the worker pool just by opening
    # streams in a loop — the outbound equivalent of ``MAX_REQUEST_BYTES``.
    # Past the cap a new subscription is refused with ``-32603`` rather than
    # queued. ``None`` disables the check.
    "MAX_CONCURRENT_SUBSCRIPTIONS": 100,
    # How long a ``requestState`` stays redeemable, in seconds.
    #
    # The clock a human is on: long enough to read a confirmation dialog, find
    # the number it is asking about and decide, short enough that a token
    # captured from a log or a proxy is dead before anyone gets round to
    # replaying it. Ten minutes is generous for a form and stingy for an
    # attacker. Expiry is one of the three replay defences the spec asks for —
    # the other two, principal and originating request, are not configurable
    # because there is no defensible value other than "enforced".
    "INPUT_REQUEST_TTL_SECONDS": 600,
    # How many times one call may ask the user for something before giving up.
    #
    # The spec explicitly allows asking repeatedly, and a service that gathers a
    # confirmation and then a reason legitimately needs two rounds. What this
    # bounds is the service that can never be satisfied — a condition that the
    # answer does not actually clear — which without a cap becomes a client and
    # a server volleying the same question at a user indefinitely. Past the cap
    # the call fails with the service's own message instead of asking again.
    "MAX_INPUT_ROUNDS": 5,
    "ALLOWED_ORIGINS": [],
    "DEFAULT_OUTPUT_FORMAT": "json",
    # The server's wire identity. Recognised keys: ``name``, ``version``,
    # ``title``, ``description``, ``websiteUrl``, ``icons``. Every one of them
    # is also a constructor kwarg on ``MCPServer`` — except ``description``,
    # which is settings-only because the constructor's ``description=`` already
    # means the ``initialize`` ``instructions`` string (see ``Implementation``).
    #
    # ``icons`` is a list of dicts mirroring the spec's ``Icon`` — ``src``
    # (required, https: or data: only), ``mimeType``, ``sizes``, ``theme``.
    "SERVER_INFO": {"name": "djangorestframework-mcp-server"},
    # Ceiling on ``notifications/progress`` frames emitted for one request.
    #
    # The spec asks both parties to rate-limit progress, and the failure mode is
    # familiar from the outbound bounds below: a service reporting per row over
    # a large table turns one call into a flood of frames, each of which costs a
    # write and some of the client's attention. Past the cap, further reports
    # are dropped — the dispatch itself is untouched, and the final response
    # still arrives.
    "MAX_PROGRESS_NOTIFICATIONS": 1_000,
    "MAX_REQUEST_BYTES": 1_048_576,
    # Ceiling on a single tool result / resource read, measured on the encoded
    # JSON-RPC ``result`` payload — the mirror of ``MAX_REQUEST_BYTES`` on the
    # outbound side. ``None`` disables the check.
    #
    # Measured on the *wire* payload, not the rendered text block, because a
    # successful tool result carries the payload twice: once as
    # ``structuredContent`` and once as the human-readable ``content[0]`` text
    # the spec asks for as a backwards-compatibility mirror. A ceiling that
    # only counted one of them would be wrong by 2× against the thing that
    # actually matters — the client's context window.
    #
    # Over the ceiling, the call comes back as an ``isError`` tool result
    # naming the remedy, **never as a silently truncated payload**. Truncation
    # is the wrong failure mode for a model consumer: a clipped list reads as
    # complete, and the model reasons from it. An error is something the model
    # can act on — narrow the filter, lower ``limit`` — which is exactly what
    # the spec means by a tool execution error carrying actionable feedback.
    "MAX_RESULT_BYTES": 5_242_880,
    # Ceiling on the model-supplied ``limit`` of a ``paginate=True`` selector
    # tool. ``None`` disables the clamp.
    #
    # Clamping rather than erroring is safe *here specifically* because a
    # paginated result is self-describing: ``totalPages`` / ``hasNext`` tell
    # the model there is more, so a clamped page is honest in a way a clipped
    # unpaginated list is not. The generated ``inputSchema`` also advertises
    # this as ``maximum`` on ``limit``, so a well-behaved model asks for
    # something serveable in the first place; the clamp is what stops us
    # trusting it.
    "MAX_PAGE_SIZE": 500,
    # Wall-clock ceiling, in seconds, on a single dispatch. ``None`` disables.
    #
    # ⚠ **Async transport only.** A sync (WSGI) view has no in-process way to
    # bound its own dispatch, so this applies to the ASGI viewset's
    # ``tools/call`` / ``resources/read`` / ``prompts/get`` paths and nowhere
    # else.
    #
    # ⚠ **This does not reclaim the worker.** A thread parked in a database
    # driver's socket read is not interruptible by asyncio cancellation, so the
    # thread stays hot until the query itself ends. What the deadline buys is a
    # *terminal protocol event*: the client learns the call failed and why,
    # instead of holding an open request until something upstream gives up. Pair
    # it with a database-level statement timeout, which is what actually ends
    # the query.
    "DISPATCH_TIMEOUT": 60.0,
    # Default canonical resource URL. This is the identity the resource server
    # *publishes* — RFC 9728 requires it in protected-resource metadata, and it
    # is what audience enforcement compares against when enabled.
    #
    # Setting it does **not** by itself reject anything: enforcement is
    # ``ENFORCE_AUDIENCE`` below. The two were once the same knob, which made a
    # deployment choose between publishing valid metadata and being able to
    # authenticate at all — see that setting's note.
    #
    # This is only the **default** for ``MCPServer(resource_url=...)``. RFC 8707
    # binds a token to *a* resource, so each server in a project needs its own
    # canonical URL — two servers sharing one URL means a token minted for one
    # passes the audience check at the other, which is the exact replay this
    # mechanism exists to prevent. Set it per server; leave this for the
    # single-server case.
    "RESOURCE_URL": None,
    # Whether a token-validating auth backend *rejects* tokens whose bound
    # resource doesn't equal ``RESOURCE_URL`` (RFC 8707 audience binding).
    #
    # Default ``False``, and that default is load-bearing rather than lax.
    # Enforcement needs the access token to record which resource it was issued
    # for, and DOT's stock ``AccessToken`` model has no such field — it
    # implements no resource indicators at all. Enforcement was previously
    # implied by ``RESOURCE_URL`` alone, so the only OAuth backend this package
    # ships rejected *every* token the moment a resource URL was configured,
    # which the MCP spec requires it to be. Nothing could authenticate.
    #
    # Turn this on when the token genuinely carries the resource: a swapped
    # ``OAUTH2_PROVIDER["ACCESS_TOKEN_MODEL"]`` with a ``resource`` field, or a
    # backend given an explicit ``audience_getter=``. The backend refuses to
    # start otherwise, rather than 401-ing every request.
    "ENFORCE_AUDIENCE": False,
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
    # Default for ``build_oauth_urlpatterns(dcr_enabled=)``. Dynamic Client
    # Registration (RFC 7591) gate. ``False`` (default) means the contrib
    # ``/oauth/register/`` endpoint refuses every request with 403. Turn on
    # only when you've thought through the abuse surface — an open DCR
    # endpoint lets anyone create an OAuth client against your authorization
    # server.
    "DCR_ENABLED": False,
    # Default for ``build_oauth_urlpatterns(dcr_initial_access_token=)``. The
    # optional initial-access-token (RFC 7591 §3) DCR clients must present in
    # ``Authorization: Bearer <token>`` to register. ``None`` means "no token
    # check" — equivalent to "anyone who can reach the endpoint can register".
    # Setting a static token is the simplest way to gate DCR behind shared
    # knowledge; rotate it manually when needed.
    "DCR_INITIAL_ACCESS_TOKEN": None,
    # Default for ``SimpleJWTCookieAdapter(cookie_name=)`` — the cookie it
    # reads access tokens from. ``"access"`` matches
    # ``djangorestframework-simplejwt``'s documented ``AUTH_COOKIE`` default.
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
    # When True, registering a tool with no description raises
    # ``ImproperlyConfigured`` instead of emitting the default
    # ``UndescribedToolWarning``. A description is not decoration: it is the
    # only thing a model reads to decide whether to call the tool, so an empty
    # one ships a tool that cannot be used correctly — the same class of
    # silent-shipping problem ``REQUIRE_TOOL_PERMISSIONS`` guards, and
    # previously the only one of the two that was checked.
    "REQUIRE_TOOL_DESCRIPTIONS": False,
    # When True, registering a LIST selector tool with ``paginate=False``
    # raises ``ImproperlyConfigured`` instead of emitting the default
    # ``UnboundedListWarning``. Such a tool serialises whatever the selector
    # returns — the whole table, if that is what the queryset resolves to —
    # and unlike a paginated tool there is no honest way to clamp it, because
    # the result carries no metadata that would tell the model rows were
    # dropped. ``MAX_RESULT_BYTES`` is the backstop; pagination is the fix.
    "REQUIRE_LIST_PAGINATION": False,
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
