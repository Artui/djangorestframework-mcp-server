from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any, cast

from django.http import HttpResponse, HttpResponseBase, JsonResponse
from django.utils.decorators import classonlymethod
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.viewsets import ViewSet
from rest_framework_services.types.progress_reporter import ProgressReporter

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp.auth.principal_for_token import principal_for_token
from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.constants import (
    MCP_ERROR_HEADER,
    MODERN_PROTOCOL_VERSIONS,
    SESSIONLESS_METHODS,
    SUBSCRIPTIONS_LISTEN_METHOD,
    JsonRpcErrorCode,
)
from rest_framework_mcp.handlers.async_dispatch import adispatch
from rest_framework_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
)
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
from rest_framework_mcp.subscriptions.grant_subscription import grant_subscription
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.subscriptions.types.subscription_filter import SubscriptionFilter
from rest_framework_mcp.tasks.types.task_executor import TaskExecutor
from rest_framework_mcp.tasks.types.task_store import TaskStore
from rest_framework_mcp.transport.negotiate_protocol_version import negotiate_protocol_version
from rest_framework_mcp.transport.origin_validation import is_origin_allowed
from rest_framework_mcp.transport.progress_dispatch import (
    can_report_progress,
    preflight_permissions,
    progress_token,
    stream_with_progress,
)
from rest_framework_mcp.transport.sse_response import build_sse_response
from rest_framework_mcp.transport.subscription_stream import build_subscription_stream
from rest_framework_mcp.transport.types.request_metadata import RequestMetadata
from rest_framework_mcp.transport.types.session_store import SessionStore
from rest_framework_mcp.transport.types.sse_broker import SSEBroker
from rest_framework_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer
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
        # Summarises the body for clients that surface only the status line.
        response[MCP_ERROR_HEADER] = error_hint
    return response


class AsyncStreamableHttpViewSet(ViewSet):
    """Async sibling of ``StreamableHttpViewSet`` for ASGI deployments.

    Wire behaviour matches the sync ViewSet for POST and DELETE — same headers,
    same status codes, same JSON-RPC shapes — and the reasoning behind the
    shared checks lives there. The async path additionally serves
    server-initiated SSE on GET when an [`SSEBroker`][rest_framework_mcp.transport.types.sse_broker.SSEBroker] is wired in; without
    one, GET returns 405, which the spec allows when there is nothing to push.

    Sync collaborators (auth backend, session store, permissions) are bridged
    through ``asgiref.sync.sync_to_async``, so a fully sync stack still
    works under ASGI.
    """

    authentication_classes: tuple = ()
    permission_classes = (AllowAny,)
    renderer_classes = (JSONRenderer,)
    view_is_async: bool = True

    tools: ToolRegistry | None = None
    resources: ResourceRegistry | None = None
    prompts: PromptRegistry | None = None
    auth_backend: MCPAuthBackend | None = None
    session_store: SessionStore | None = None
    # Async-only: a subscription is a stream that stays open, which a sync WSGI
    # view cannot hold. The sync viewset is deliberately not given one.
    subscription_broker: SubscriptionBroker | None = None
    # ``None`` on both means this server runs no tasks.
    task_store: TaskStore | None = None
    task_executor: TaskExecutor | None = None
    sse_broker: SSEBroker | None = None
    sse_replay_buffer: SSEReplayBuffer | None = None
    # Identity the owning server resolved at construction. Unlike the
    # collaborators above these stay optional at dispatch: a hand-wired viewset
    # with no server still answers ``initialize``, falling back to
    # ``SERVER_INFO``.
    server_info: Implementation | None = None
    instructions: str | None = None
    config: MCPConfig | None = None

    @classonlymethod
    def as_view(cls, actions: Any = None, **initkwargs: Any) -> Any:
        """Wrap DRF's sync ``as_view`` so Django routes the callable as async.

        ``ViewSet.as_view`` returns a sync function calling
        ``self.dispatch(request)``; because our ``dispatch`` is ``async def``,
        that wrapper hands Django an unawaited coroutine and the request
        handler raises. Wrapping and tagging it with the ``_is_coroutine``
        marker tells the handler to ``await`` the view instead.
        """
        sync_view = super().as_view(actions=actions, **initkwargs)

        async def async_view(request: Any, *args: Any, **kwargs: Any) -> HttpResponseBase:
            # Our overridden ``dispatch`` is async, so this is a coroutine the
            # DRF stub does not know about; the cast bypasses its ``Response``
            # return-type narrowing.
            return await cast(Any, sync_view(request, *args, **kwargs))

        # Carries over the introspection attributes and, load-bearingly, the
        # middleware opt-out flags: ``CsrfViewMiddleware`` and
        # ``LoginRequiredMiddleware`` read ``csrf_exempt`` / ``login_required``
        # off the *resolved callable*, which is this wrapper rather than the
        # sync view underneath, so without them a POST or DELETE 403s on a
        # missing CSRF token or 302s to the login page — for a bearer-token
        # transport that has neither. Copied wholesale so a future DRF/Django
        # attribute cannot silently go missing; dunders are skipped because
        # ``__wrapped__`` would point introspection at the sync view.
        for attr, value in sync_view.__dict__.items():
            if not attr.startswith("__"):
                setattr(async_view, attr, value)
        # Django's marker for "this callable returns a coroutine", so
        # ``django.core.handlers.base`` awaits it. The underscore-prefixed name
        # is a private asyncio detail, but Django relies on it stably.
        async_view._is_coroutine = (  # ty: ignore[unresolved-attribute]
            asyncio.coroutines._is_coroutine  # ty: ignore[unresolved-attribute]
        )
        return async_view

    async def dispatch(  # ty: ignore[invalid-method-override]
        self, request: Any, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Async-aware mirror of ``rest_framework.views.APIView.dispatch``.

        DRF's stock ``dispatch`` is sync: it passes whatever the handler
        returns to ``finalize_response``, which ``isinstance``-checks against
        ``HttpResponseBase`` and so chokes on the coroutine an ``async def``
        action returns. DRF 3.16+ added async hooks at the framework edges but
        the ViewSet dispatch path still assumes sync, so until that lands
        upstream this override is what avoids an ``async_to_sync`` thread-hop.
        """
        self.args = args
        self.kwargs = kwargs
        request = self.initialize_request(request, *args, **kwargs)
        self.request = request
        self.headers = self.default_response_headers

        try:
            self.initial(request, *args, **kwargs)
            # The DRF stub types ``request.method`` as ``str | None``; inside
            # an HTTP request flow it is always populated.
            method: str = request.method.lower()  # ty: ignore[unresolved-attribute]
            handler = getattr(self, method, self.http_method_not_allowed)
            result = handler(request, *args, **kwargs)
            # Action handlers are async, but the base class's
            # ``http_method_not_allowed`` is sync — accept either shape so a
            # stray sync handler does not 500.
            response = await result if asyncio.iscoroutine(result) else result
        except Exception as exc:
            response = self.handle_exception(exc)

        # ``finalize_response`` is typed ``response: Response`` on the stub but
        # accepts ``HttpResponseBase`` at runtime, short-circuiting for
        # non-Response shapes — which is what our actions deliberately return.
        self.response = self.finalize_response(request, cast(Any, response), *args, **kwargs)
        return self.response

    # ----- DRF async action methods -----

    async def handle_jsonrpc(self, request: Request) -> HttpResponseBase:
        http_request = request._request  # noqa: SLF001
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
        # Answerable without a session, but does *not* mint one — hence a
        # second flag rather than a widening of the first.
        is_sessionless: bool = (
            isinstance(message, JsonRpcRequest) and message.method in SESSIONLESS_METHODS
        )

        # **The era fork** — see the sync sibling for the reasoning.
        metadata: RequestMetadata | None = RequestMetadata.from_params(
            _params_dict(getattr(message, "params", None))
        )
        if metadata is not None:
            return await self._handle_modern(http_request, message, metadata)

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

        # Before the session lookup, so session ids cannot be probed through a
        # 404-vs-401 oracle. See the sync sibling.
        token = await self._authenticate(http_request)
        if token is None:
            logger.warning("Authentication failed for %s", http_request.path)
            return self._unauthenticated_response()

        # A wrong-principal presentation renders the same 404 as an unknown id,
        # so the gate is not an ownership oracle.
        store = self._require_session_store()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        principal: str = principal_for_token(token)
        if self._sessions_enabled() and not is_sessionless:
            owner_matches = bool(session_id) and await acall(store.owner, session_id) == principal
            failure = session_gate_failure(session_id, owner_matches=owner_matches)
            if failure is not None:
                message_text, status, hint = failure
                # The log names the exact condition the response merges.
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
            subscriptions=self.subscription_broker,
            config=self._require_config(),
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        if not isinstance(message, JsonRpcRequest):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST, message="Expected a JSON-RPC request"
            )

        streamed: HttpResponseBase | None = await self._maybe_stream(message, context)
        if streamed is not None:
            return streamed

        result: Any = await adispatch(message.method, _params_dict(message.params), context)

        if isinstance(result, JsonRpcError):
            response_body = JsonRpcResponse(id=message.id, error=result).to_dict()
        else:
            response_body = JsonRpcResponse(id=message.id, result=result).to_dict()
        # 403 plus a challenge naming the missing scopes: the MCP authorization
        # spec's error table makes the status normative. Every other outcome,
        # including a tool that failed on its own terms, stays a 200.
        denied: bool = is_permission_denial(result)
        http_response = JsonResponse(response_body, status=403 if denied else 200)
        if denied:
            http_response["WWW-Authenticate"] = insufficient_scope_challenge(
                result, self._require_auth_backend()
            )

        if is_initialize and self._sessions_enabled() and not isinstance(result, JsonRpcError):
            # Assignment is the server's choice; the client's duty to echo one
            # is conditional on it arriving. Not minting is sessionless mode.
            new_session: str = await acall(store.create, principal_id=principal)
            http_response[_SESSION_HEADER] = new_session
        return http_response

    async def _handle_modern(
        self, http_request: Any, message: Any, metadata: RequestMetadata
    ) -> HttpResponseBase:
        """Async sibling of the sync viewset's modern path — same rules.

        A full parallel implementation rather than a wrapper, as everywhere
        else in this transport: threading a sync/async switch through would put
        the era branch somewhere it does not belong.
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

        token = await self._authenticate(http_request)
        if token is None:
            return self._unauthenticated_response()

        context = MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._require_tools(),
            resources=self._require_resources(),
            prompts=self._require_prompts(),
            protocol_version=metadata.protocol_version,
            session_id=None,
            # Modern path only: the spec forbids relying on a declaration that
            # did not arrive *with the request*, so the legacy context leaves
            # this empty.
            client_capabilities=metadata.client_capabilities,
            server_info=self.server_info,
            instructions=self.instructions,
            tasks=self.task_store,
            task_executor=self.task_executor,
            subscriptions=self.subscription_broker,
            config=config,
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        # Necessarily a request by now. The era test reads ``params``, which a
        # JSON-RPC *response* does not carry, so a response body is always
        # routed to the legacy path — and rejected there.
        subscribed: HttpResponseBase | None = await self._maybe_subscribe(message, context)
        if subscribed is not None:
            return subscribed

        streamed: HttpResponseBase | None = await self._maybe_stream(message, context)
        if streamed is not None:
            return streamed

        result: Any = await adispatch(message.method, _params_dict(message.params), context)
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

    async def handle_get(self, request: Request) -> HttpResponseBase:
        """GET action: open a server-pushed SSE stream for the current session.

        - 401 if the caller cannot authenticate — the stream carries a
          session's payloads, so it is gated exactly like POST.
        - 405 if no broker is configured (nothing to push).
        - 400 if the protocol-version header is missing or unsupported. The
          spec is silent on GET here; this is parity with POST.
        - 404 if the session id is unknown **or owned by a different
          principal** (indistinguishable on purpose).
        - Otherwise ``text/event-stream``, with idle keep-alives and whatever
          ``MCPServer.notify`` enqueues.
        """
        http_request = request._request  # noqa: SLF001
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard
        if self._modern_era_requested(http_request):
            return HttpResponse(status=405)

        token = await self._authenticate(http_request)
        if token is None:
            return self._unauthenticated_response()

        if self.sse_broker is None or not self._sessions_enabled():
            # The session id *is* the channel address, so sessionless leaves
            # nothing to open a per-client stream against. 405 is the spec's
            # status for "this endpoint offers no SSE stream".
            return HttpResponse(status=405)

        version_header: str | None = http_request.headers.get(_VERSION_HEADER)
        if (
            negotiate_protocol_version(
                version_header, is_sessionless=False, config=self._require_config()
            )
            is None
        ):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Missing or unsupported MCP-Protocol-Version",
            )

        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        store = self._require_session_store()
        if not session_id or await acall(store.owner, session_id) != principal_for_token(token):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Unknown or missing MCP-Session-Id",
                status=404,
            )

        # ``Last-Event-ID`` is honoured only with a replay buffer wired in;
        # otherwise there is nothing buffered to replay and it is ignored.
        last_event_id: str | None = (
            http_request.headers.get("Last-Event-ID")
            if self.sse_replay_buffer is not None
            else None
        )
        return build_sse_response(
            self.sse_broker,
            session_id,
            replay_buffer=self.sse_replay_buffer,
            last_event_id=last_event_id,
        )

    async def terminate_session(self, request: Request) -> HttpResponse:
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
            # No sessions to terminate — the spec's blessed answer for a server
            # that does not let clients terminate sessions.
            return HttpResponse(status=405)
        token = await self._authenticate(http_request)
        if token is None:
            return self._unauthenticated_response()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        if session_id:
            store = self._require_session_store()
            if await acall(store.owner, session_id) != principal_for_token(token):
                return _error_response(
                    code=JsonRpcErrorCode.INVALID_REQUEST,
                    message="Unknown or missing MCP-Session-Id",
                    status=404,
                )
            # The session is going away, so buffered events would never be
            # delivered.
            if self.sse_replay_buffer is not None:
                await self.sse_replay_buffer.forget(session_id)
            await acall(store.destroy, session_id)
        return HttpResponse(status=204)

    async def _maybe_subscribe(
        self, message: Any, context: MCPCallContext
    ) -> HttpResponseBase | None:
        """Answer ``subscriptions/listen`` with a stream that stays open.

        ``None`` for every other method. Separate from ``_maybe_stream``: that
        one wraps a dispatch and ends with its result, while this has no
        dispatch and ends when the client leaves.

        **Modern era only.** A legacy client reaching here gets dispatch's
        unknown-method answer rather than a stream it cannot interpret.
        """
        if getattr(message, "method", None) != SUBSCRIPTIONS_LISTEN_METHOD:
            return None
        broker = context.subscriptions
        if broker is None:
            # Checked before granting: evaluating permissions and reading the
            # task store to build a grant we then discard is work for nothing.
            # An empty grant closes after the acknowledgement.
            return build_subscription_stream(
                broker=InMemorySubscriptionBroker(),
                topics=frozenset(),
                granted=SubscriptionFilter(),
                request_id=message.id,
                max_seconds=context.config.subscription_max_seconds,
            )

        cap: int | None = context.config.max_concurrent_subscriptions
        if cap is not None and broker.active_subscriptions >= cap:
            # Refused rather than queued. Each subscription parks a worker for
            # its lifetime, so accepting past the cap trades a clear error for a
            # server that stops answering anything.
            return _error_response(
                code=JsonRpcErrorCode.INTERNAL_ERROR,
                message=(
                    f"This server is already serving its maximum of {cap} concurrent "
                    "subscriptions. Retry shortly, or raise "
                    "REST_FRAMEWORK_MCP['MAX_CONCURRENT_SUBSCRIPTIONS']."
                ),
                status=503,
                request_id=message.id,
            )

        params: dict[str, Any] | None = _params_dict(message.params)
        requested = SubscriptionFilter.from_params(
            params.get("notifications") if params is not None else None
        )
        # The spec's one exception to "refused entries are dropped": a client
        # asking for task notifications without declaring the tasks extension
        # MUST get an error, not a quiet omission. Answered here rather than in
        # the grant because it produces an error response instead of a stream.
        if requested.task_ids and not declares_tasks_extension(context):
            denied = missing_capability_error()
            return _error_response(
                code=denied.code,
                message=denied.message,
                data=denied.data,
                status=modern_error_status(denied),
                request_id=message.id,
            )
        granted, topics = await acall(grant_subscription, requested, context)
        return build_subscription_stream(
            broker=broker,
            topics=topics,
            granted=granted,
            request_id=message.id,
            max_seconds=context.config.subscription_max_seconds,
        )

    async def _maybe_stream(self, message: Any, context: MCPCallContext) -> HttpResponseBase | None:
        """Answer with an SSE stream when the client asked to hear about progress.

        ``None`` means "not this request" — no ``progressToken``, so a single
        JSON object it is. The spec lets the server choose per request, and a
        stream whose only event is the final response costs a connection and
        buys nothing.

        Era-independent: the token sits at ``_meta.progressToken`` in both.
        """
        params: dict[str, Any] | None = _params_dict(message.params)
        token: str | int | None = progress_token(params)
        if token is None:
            return None

        # Asking is not enough — the dispatch has to be able to answer. See
        # ``can_report_progress`` for which paths qualify and why.
        if not await acall(can_report_progress, message.method, params, context):
            return None

        # Before the stream opens, while a status can still be chosen. See
        # ``preflight_permissions`` for why this one check moves out here.
        denied: JsonRpcError | None = await acall(
            preflight_permissions, message.method, params, context
        )
        if denied is not None:
            response = _error_response(
                code=denied.code,
                message=denied.message,
                data=denied.data,
                status=403,
                request_id=message.id,
            )
            response["WWW-Authenticate"] = insufficient_scope_challenge(
                denied, self._require_auth_backend()
            )
            return response

        def dispatch_with(reporter: ProgressReporter) -> Any:
            return adispatch(
                message.method, params, dataclasses.replace(context, progress=reporter)
            )

        return stream_with_progress(
            dispatch=dispatch_with, request_id=message.id, token=token, context=context
        )

    def _modern_era_requested(self, http_request: Any) -> bool:
        """Whether the caller named a modern revision in its version header.

        GET and DELETE carry no body, so the per-request ``_meta`` that decides
        the era elsewhere is unavailable and the header is the only signal.
        ``2026-07-28`` removed both the GET stream and session termination, so
        a caller naming that revision and using either verb gets ``405``.
        """
        version: str | None = http_request.headers.get(_VERSION_HEADER)
        return version in MODERN_PROTOCOL_VERSIONS

    # ----- collaborator accessors -----

    async def _authenticate(self, http_request: Any) -> Any:
        backend = self._require_auth_backend()
        return await acall(backend.authenticate, http_request)

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
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a ToolRegistry")
        return self.tools

    def _require_resources(self) -> ResourceRegistry:
        if self.resources is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a ResourceRegistry")
        return self.resources

    def _require_prompts(self) -> PromptRegistry:
        if self.prompts is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a PromptRegistry")
        return self.prompts

    def _require_auth_backend(self) -> MCPAuthBackend:
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing an MCPAuthBackend")
        return self.auth_backend

    def _require_config(self) -> MCPConfig:
        if self.config is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing an MCPConfig")
        return self.config

    def _sessions_enabled(self) -> bool:
        """Whether the legacy era mints and requires an ``Mcp-Session-Id``."""
        return self._require_config().sessions_enabled

    def _require_session_store(self) -> SessionStore:
        if self.session_store is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a SessionStore")
        return self.session_store


def _params_dict(params: Any) -> dict[str, Any] | None:
    if params is None:
        return None
    if isinstance(params, dict):
        return params
    return None  # JSON-RPC list params are not used by MCP today.


# Passed directly to ``as_view``, so the canonical mapping lives next to the
# ViewSet rather than in the URL conf.
ASYNC_STREAMABLE_HTTP_ACTION_MAP: dict[str, str] = {
    "post": "handle_jsonrpc",
    "get": "handle_get",
    "delete": "terminate_session",
}


__all__ = ["ASYNC_STREAMABLE_HTTP_ACTION_MAP", "AsyncStreamableHttpViewSet"]
