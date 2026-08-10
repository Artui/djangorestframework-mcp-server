from __future__ import annotations

import json
from typing import Any

from django.http import HttpResponse, JsonResponse
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.viewsets import ViewSet

from rest_framework_mcp._compat.reject_awaitable import reject_awaitable
from rest_framework_mcp.auth.principal_for_token import principal_for_token
from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.constants import (
    MCP_ERROR_HEADER,
    MODERN_PROTOCOL_VERSIONS,
    SESSIONLESS_METHODS,
    JsonRpcErrorCode,
)
from rest_framework_mcp.handlers.dispatch import dispatch
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.observability import get_logger, session_fingerprint
from rest_framework_mcp.protocol.parse_message import parse_message
from rest_framework_mcp.protocol.types.implementation import Implementation
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.json_rpc_notification import JsonRpcNotification
from rest_framework_mcp.protocol.types.json_rpc_request import JsonRpcRequest
from rest_framework_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.tasks.types.task_executor import TaskExecutor
from rest_framework_mcp.tasks.types.task_store import TaskStore
from rest_framework_mcp.transport.negotiate_protocol_version import negotiate_protocol_version
from rest_framework_mcp.transport.origin_validation import is_origin_allowed
from rest_framework_mcp.transport.types.request_metadata import RequestMetadata
from rest_framework_mcp.transport.types.session_store import SessionStore
from rest_framework_mcp.transport.utils import (
    insufficient_scope_challenge,
    is_permission_denial,
    modern_error_status,
    session_gate_failure,
)
from rest_framework_mcp.transport.validate_modern_request import validate_modern_request

_SESSION_HEADER: str = "Mcp-Session-Id"
_VERSION_HEADER: str = "Mcp-Protocol-Version"


logger = get_logger(__name__)


def _error_response(
    *,
    code: int,
    message: str,
    data: Any = None,
    status: int = 400,
    request_id: Any = None,
    error_hint: str | None = None,
) -> JsonResponse:
    body: dict[str, Any] = JsonRpcResponse(
        id=request_id, error=JsonRpcError(code=code, message=message, data=data)
    ).to_dict()
    response = JsonResponse(body, status=status)
    if error_hint is not None:
        # Summarises the body for the many clients that surface only the status
        # line — see ``MCP_ERROR_HEADER``.
        response[MCP_ERROR_HEADER] = error_hint
    return response


def _reject_awaitable_token(result: Any, *, backend: Any) -> Any:
    """Return the token, or refuse an ``authenticate`` that must be awaited.

    ⚠ **This is a security gate, not a type check.** The sync transport cannot
    await anything, so an ``async def authenticate`` mounted under
    ``server.urls`` hands back an un-awaited coroutine — and a coroutine object
    is *truthy*, so every ``token is None`` check downstream passes and every
    caller is authenticated. ``principal_for_token`` then reads no ``pk`` off
    it and files the request under the shared ``"anonymous"`` principal. The
    endpoint is wide open and nothing in the response says so.

    Failing the request loudly is the only safe reading: the caller presented
    credentials nobody verified. The backend is fine — its *mounting* is wrong,
    so the message names both ways out.
    """
    return reject_awaitable(
        result,
        call=f"{type(backend).__name__}.authenticate()",
        remedy=(
            "This request was served by the sync MCP transport (server.urls), which "
            "cannot await it. Mount the server under server.async_urls (ASGI) so the "
            "backend is awaited, or make authenticate a plain 'def'."
        ),
        hazard=(
            "an un-awaited coroutine is truthy, so serving it would authenticate every caller."
        ),
    )


def _reject_awaitable_session(result: Any, *, store: Any, method: str) -> Any:
    """Return a session-store answer, or refuse one that must be awaited.

    ⚠ **The likeliest instance of this defect class, precisely because async
    stores are supported.** The *async* transport ``acall``s these methods, so
    an ``async def owner`` is a correct, documented implementation there — and
    the same store mounted under ``server.urls`` silently stops working. Unlike
    the auth and permission sites this one fails *closed* (a coroutine equals
    no principal, so ownership never matches), but it fails closed
    incomprehensibly: every request answers "re-initialize", and a session id
    minted from :meth:`create` is the ``repr`` of a coroutine.

    Naming the mounting is the whole value here — the store is not the bug.
    """
    return reject_awaitable(
        result,
        call=f"{type(store).__name__}.{method}()",
        remedy=(
            "Session stores may be async only under the async transport, which awaits "
            "them; this request was served by the sync transport (server.urls). Mount "
            f"the server under server.async_urls (ASGI), or make {method} a plain 'def'."
        ),
        hazard=(
            "an un-awaited coroutine matches no principal and is not a usable session "
            "id, so every subsequent request would be rejected as un-initialized."
        ),
    )


class StreamableHttpViewSet(ViewSet):
    """The single ``/mcp`` endpoint per MCP 2025-11-25 (Streamable HTTP).

    Wires three HTTP methods to one URL through DRF's ViewSet action map
    (``StreamableHttpViewSet.as_view({"post": "handle_jsonrpc", "get":
    "handle_get", "delete": "terminate_session"}, ...)``):

    - **POST** → exactly one JSON-RPC message, returns ``application/json``
      (or HTTP 202 for notifications).
    - **GET** → not implemented in v1; returns 405 (allowed by spec).
    - **DELETE** → terminates the session referenced by ``MCP-Session-Id``.

    The transport bypasses DRF's default request lifecycle on purpose:
    ``authentication_classes`` is empty because :class:`MCPAuthBackend`
    is the auth layer (DRF's ``SessionAuthentication`` would fight with
    the bearer-token shape MCP advertises). ``permission_classes`` is
    :class:`AllowAny` because per-binding permissions live on the
    registered tool / resource / prompt — the transport itself doesn't
    gate. Renderers / parsers stay minimal because the JSON-RPC envelope
    is RFC-defined and we serialise it explicitly via :class:`JsonResponse`.

    The view's collaborators (registries, auth backend, session store) are
    instance-scoped — passed in via :class:`MCPServer` through ``as_view``.
    There is no module-level lookup for any of them, which keeps multiple
    independent servers from interfering with each other in one process.
    """

    authentication_classes: tuple = ()
    permission_classes = (AllowAny,)
    renderer_classes = (JSONRenderer,)

    # ``as_view`` requires kwargs to be existing class attributes;
    # declaring them as None defaults lets the server pass populated
    # collaborators in.
    tools: ToolRegistry | None = None
    resources: ResourceRegistry | None = None
    prompts: PromptRegistry | None = None
    auth_backend: MCPAuthBackend | None = None
    session_store: SessionStore | None = None
    # Supplied by ``MCPServer.as_view(...)``, like every other collaborator —
    # never looked up from module scope. ``None`` on both means this server
    # runs no tasks, which is the default and changes nothing.
    task_store: TaskStore | None = None
    task_executor: TaskExecutor | None = None
    # Identity the owning server resolved at construction. Unlike the
    # collaborators above these stay optional at dispatch: a hand-wired viewset
    # with no server still answers ``initialize``, falling back to
    # ``SERVER_INFO``.
    server_info: Implementation | None = None
    instructions: str | None = None
    # The owning server's resolved scalars, supplied by MCPServer like
    # every other collaborator — never looked up from settings here.
    config: MCPConfig | None = None

    # ----- DRF action methods (mapped via ``as_view({...})``) -----

    def handle_jsonrpc(self, request: Request) -> HttpResponse:
        """POST action: parse a single JSON-RPC message and dispatch."""
        http_request = request._request  # noqa: SLF001 — unwrap DRF Request for legacy helpers
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard

        max_bytes: int = self._require_config().max_request_bytes
        if len(http_request.body) > max_bytes:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Request body too large",
                status=413,
            )

        try:
            payload: Any = json.loads(http_request.body or b"null")
        except json.JSONDecodeError as exc:
            return _error_response(
                code=JsonRpcErrorCode.PARSE_ERROR, message=f"Invalid JSON: {exc.msg}"
            )

        try:
            message = parse_message(payload)
        except ValueError as exc:
            return _error_response(code=JsonRpcErrorCode.INVALID_REQUEST, message=str(exc))

        is_initialize: bool = isinstance(message, JsonRpcRequest) and message.method == "initialize"
        # ``server/discover`` joins ``initialize`` in being answerable without a
        # session — a client sends it precisely because it has nothing yet. It
        # does *not* mint one, which is why this is a second flag rather than a
        # widening of the first.
        is_sessionless: bool = (
            isinstance(message, JsonRpcRequest) and message.method in SESSIONLESS_METHODS
        )

        # ⭐ **The era fork.** A dual-era server picks its behaviour from how the
        # client opened: per-request ``_meta`` carrying a protocol version means
        # modern (stateless, header-validated), its absence means legacy
        # (``initialize`` handshake, sessions). One branch, here, at the edge —
        # everything below the transport is era-agnostic by construction.
        metadata: RequestMetadata | None = RequestMetadata.from_params(
            _params_dict(getattr(message, "params", None))
        )
        if metadata is not None:
            return self._handle_modern(http_request, message, metadata)

        version_header: str | None = http_request.headers.get(_VERSION_HEADER)
        negotiated: str | None = negotiate_protocol_version(
            version_header, is_sessionless=is_sessionless, config=self._require_config()
        )
        if negotiated is None:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Missing or unsupported MCP-Protocol-Version",
                request_id=getattr(message, "id", None),
            )
        protocol_version: str = negotiated

        # Authentication runs *before* the session lookup: an unauthenticated
        # caller always sees 401 regardless of session validity, so session
        # ids cannot be probed via a 404-vs-401 oracle. Origin / size /
        # protocol-version checks above are not principal-revealing.
        token = self._authenticate(http_request)
        if token is None:
            logger.warning("Authentication failed for %s", http_request.path)
            return self._unauthenticated_response()

        # A session is bound to the principal it was minted for at
        # ``initialize``; a wrong-principal presentation renders the same
        # 404 as an unknown id (no fresh ownership oracle).
        store = self._require_session_store()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        principal: str = principal_for_token(token)
        if self._sessions_enabled() and not is_sessionless:
            owner_matches = (
                bool(session_id)
                and _reject_awaitable_session(store.owner(session_id), store=store, method="owner")
                == principal
            )
            failure = session_gate_failure(session_id, owner_matches=owner_matches)
            if failure is not None:
                message_text, status, hint = failure
                # ⭐ Server-side we name the *exact* condition. The response
                # merges unknown-id with wrong-principal so the gate is not an
                # ownership oracle, but the operator is not that adversary and
                # a log line is not the wire. This is the line that ends the
                # "is it the session or the load balancer?" incident.
                logger.warning(
                    "Session rejected: %s (session=%s, principal=%s, method=%s) -> HTTP %s",
                    hint,
                    session_fingerprint(session_id),
                    principal,
                    getattr(message, "method", "?"),
                    status,
                )
                return _error_response(
                    code=JsonRpcErrorCode.INVALID_REQUEST,
                    message=message_text,
                    status=status,
                    request_id=getattr(message, "id", None),
                    error_hint=hint,
                )

        context = MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._require_tools(),
            resources=self._require_resources(),
            prompts=self._require_prompts(),
            protocol_version=protocol_version,
            session_id=session_id,
            server_info=self.server_info,
            instructions=self.instructions,
            tasks=self.task_store,
            task_executor=self.task_executor,
            config=self._require_config(),
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        if not isinstance(message, JsonRpcRequest):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST, message="Expected a JSON-RPC request"
            )

        result: Any = dispatch(message.method, _params_dict(message.params), context)

        if isinstance(result, JsonRpcError):
            response_body = JsonRpcResponse(id=message.id, error=result).to_dict()
        else:
            response_body = JsonRpcResponse(id=message.id, result=result).to_dict()
        # A permission denial is a 403 with a challenge naming the missing
        # scopes, not a 200 with the error tucked inside. The MCP authorization
        # spec's error table makes the status normative, and the challenge is
        # how a client learns what to ask for instead of retrying the same
        # token. Every other dispatch outcome — including a tool that failed on
        # its own terms — stays a 200.
        denied: bool = is_permission_denial(result)
        http_response = JsonResponse(response_body, status=403 if denied else 200)
        if denied:
            http_response["WWW-Authenticate"] = insufficient_scope_challenge(
                result, self._require_auth_backend()
            )

        if is_initialize and self._sessions_enabled() and not isinstance(result, JsonRpcError):
            # Assignment is the server's choice ("MAY assign a session ID"), and
            # the client's duty to echo one is conditional on it arriving. Not
            # minting is therefore the whole of sessionless mode on this path.
            new_session: str = _reject_awaitable_session(
                store.create(principal_id=principal), store=store, method="create"
            )
            http_response[_SESSION_HEADER] = new_session
        return http_response

    def _handle_modern(
        self, http_request: Any, message: Any, metadata: RequestMetadata
    ) -> HttpResponse:
        """Serve one request under the stateless (``2026-07-28``) rules.

        Everything the legacy path does with a session is simply absent here:
        no lookup, no minting, no echo. An ``Mcp-Session-Id`` a legacy-minded
        client sends anyway is ignored rather than rejected, which is what the
        spec asks of a modern server receiving older traffic.

        Header validation runs **before** authentication, unlike the session
        check on the legacy path. A header/body mismatch is a malformed request
        that reveals nothing about who is asking — and it is the one signal a
        client uses to tell a modern server from a legacy one, so making it
        conditional on credentials would break era detection for anonymous
        probes.
        """
        config: MCPConfig = self._require_config()
        request_id: Any = getattr(message, "id", None)
        if isinstance(message, JsonRpcRequest):
            invalid: JsonRpcError | None = validate_modern_request(
                method=message.method,
                params=_params_dict(message.params),
                metadata=metadata,
                headers=http_request.headers,
                supported_versions=config.modern_protocol_versions,
            )
            if invalid is not None:
                return _error_response(
                    code=invalid.code,
                    message=invalid.message,
                    data=invalid.data,
                    status=modern_error_status(invalid),
                    request_id=request_id,
                )

        token = self._authenticate(http_request)
        if token is None:
            return self._unauthenticated_response()

        context = MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._require_tools(),
            resources=self._require_resources(),
            prompts=self._require_prompts(),
            protocol_version=metadata.protocol_version,
            # No session exists to name. The field stays on the context because
            # the legacy path still populates it; modern spans simply omit it.
            session_id=None,
            # ⚠ Modern path only. The legacy context leaves this empty, which is
            # correct rather than a gap: a legacy client declared its
            # capabilities once, at ``initialize``, and the spec forbids relying
            # on a declaration that did not arrive *with the request*.
            client_capabilities=metadata.client_capabilities,
            server_info=self.server_info,
            instructions=self.instructions,
            tasks=self.task_store,
            task_executor=self.task_executor,
            config=config,
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        # Necessarily a request by now. The era test reads ``params``, which a
        # JSON-RPC *response* does not carry, so a response body is always
        # routed to the legacy path — and rejected there.
        result: Any = dispatch(message.method, _params_dict(message.params), context)
        if isinstance(result, JsonRpcError):
            body = JsonRpcResponse(id=message.id, error=result).to_dict()
            status: int = modern_error_status(result)
            response = JsonResponse(body, status=status)
            if status == 403:
                response["WWW-Authenticate"] = insufficient_scope_challenge(
                    result, self._require_auth_backend()
                )
            return response
        return JsonResponse(JsonRpcResponse(id=message.id, result=result).to_dict())

    def handle_get(self, request: Request) -> HttpResponse:
        """GET action: SSE-from-server isn't implemented in v1; 405 per spec.

        Authentication still runs first so the endpoint never reveals
        anything (even its 405) to unauthenticated callers — parity with
        the async sibling's SSE stream.
        """
        http_request = request._request  # noqa: SLF001
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard
        if self._authenticate(http_request) is None:
            return self._unauthenticated_response()
        return HttpResponse(status=405)

    def terminate_session(self, request: Request) -> HttpResponse:
        """DELETE action: end the session named by ``MCP-Session-Id``.

        Requires authentication, and only the principal a session was
        minted for can destroy it — a wrong-principal (or unknown) id
        renders 404 without touching the session.
        """
        http_request = request._request  # noqa: SLF001
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard
        if self._modern_era_requested(http_request) or not self._sessions_enabled():
            # "The server MAY respond to this request with HTTP 405 Method Not
            # Allowed, indicating that the server does not allow clients to
            # terminate sessions" — which is exactly true when there are none.
            return HttpResponse(status=405)
        token = self._authenticate(http_request)
        if token is None:
            return self._unauthenticated_response()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        if session_id:
            store = self._require_session_store()
            owner = _reject_awaitable_session(store.owner(session_id), store=store, method="owner")
            if owner != principal_for_token(token):
                return _error_response(
                    code=JsonRpcErrorCode.INVALID_REQUEST,
                    message="Unknown or missing MCP-Session-Id",
                    status=404,
                )
            _reject_awaitable_session(store.destroy(session_id), store=store, method="destroy")
        return HttpResponse(status=204)

    def _modern_era_requested(self, http_request: Any) -> bool:
        """Whether the caller named a modern revision in its version header.

        GET and DELETE carry no body, so the per-request ``_meta`` that decides
        the era everywhere else is unavailable — the header is the only signal
        there is. A modern client should never send either verb; answering
        ``405`` when it does is what the spec asks of a server receiving
        wrong-era traffic, and it is a clearer diagnostic than silently
        serving a mechanism the caller's revision removed.
        """
        version: str | None = http_request.headers.get(_VERSION_HEADER)
        return version in MODERN_PROTOCOL_VERSIONS

    # ----- collaborator accessors -----

    def _authenticate(self, http_request: Any) -> Any:
        backend: MCPAuthBackend = self._require_auth_backend()
        return _reject_awaitable_token(backend.authenticate(http_request), backend=backend)

    def _unauthenticated_response(self) -> JsonResponse:
        challenge: str = self._require_auth_backend().www_authenticate_challenge(
            error="invalid_token"
        )
        response = JsonResponse(
            {"error": "unauthorized", "error_description": "Authentication required."},
            status=401,
        )
        response["WWW-Authenticate"] = challenge
        return response

    def _check_origin(self, request: Any) -> HttpResponse | None:
        origin: str | None = request.headers.get("Origin")
        if not is_origin_allowed(origin, self._require_config().allowed_origins):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message=f"Origin not allowed: {origin!r}",
                status=403,
            )
        return None

    def _require_tools(self) -> ToolRegistry:
        if self.tools is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpViewSet is missing a ToolRegistry")
        return self.tools

    def _require_resources(self) -> ResourceRegistry:
        if self.resources is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpViewSet is missing a ResourceRegistry")
        return self.resources

    def _require_prompts(self) -> PromptRegistry:
        if self.prompts is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpViewSet is missing a PromptRegistry")
        return self.prompts

    def _require_auth_backend(self) -> MCPAuthBackend:
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpViewSet is missing an MCPAuthBackend")
        return self.auth_backend

    def _require_config(self) -> MCPConfig:
        if self.config is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpViewSet is missing an MCPConfig")
        return self.config

    def _sessions_enabled(self) -> bool:
        """Whether the legacy era mints and requires an ``Mcp-Session-Id``."""
        return self._require_config().sessions_enabled

    def _require_session_store(self) -> SessionStore:
        if self.session_store is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("StreamableHttpViewSet is missing a SessionStore")
        return self.session_store


def _params_dict(params: Any) -> dict[str, Any] | None:
    if params is None:
        return None
    if isinstance(params, dict):
        return params
    return None  # JSON-RPC list params are not used by MCP today.


# Action map convenience: pass directly to ``as_view`` so the URL conf
# stays compact and the canonical mapping lives next to the ViewSet.
STREAMABLE_HTTP_ACTION_MAP: dict[str, str] = {
    "post": "handle_jsonrpc",
    "get": "handle_get",
    "delete": "terminate_session",
}


__all__ = ["STREAMABLE_HTTP_ACTION_MAP", "StreamableHttpViewSet"]
