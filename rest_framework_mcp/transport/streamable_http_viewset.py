from __future__ import annotations

import json
from typing import Any

from django.http import HttpResponse, JsonResponse
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.viewsets import ViewSet

from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.dispatch import dispatch
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.parse_message import parse_message
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.json_rpc_notification import JsonRpcNotification
from rest_framework_mcp.protocol.types.json_rpc_request import JsonRpcRequest
from rest_framework_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.negotiate_protocol_version import negotiate_protocol_version
from rest_framework_mcp.transport.origin_validation import is_origin_allowed
from rest_framework_mcp.transport.types.session_store import SessionStore

_SESSION_HEADER: str = "Mcp-Session-Id"
_VERSION_HEADER: str = "Mcp-Protocol-Version"


def _error_response(
    *, code: int, message: str, data: Any = None, status: int = 400, request_id: Any = None
) -> JsonResponse:
    body: dict[str, Any] = JsonRpcResponse(
        id=request_id, error=JsonRpcError(code=code, message=message, data=data)
    ).to_dict()
    return JsonResponse(body, status=status)


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

    # ----- DRF action methods (mapped via ``as_view({...})``) -----

    def handle_jsonrpc(self, request: Request) -> HttpResponse:
        """POST action: parse a single JSON-RPC message and dispatch."""
        http_request = request._request  # noqa: SLF001 — unwrap DRF Request for legacy helpers
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard

        max_bytes: int = int(get_setting("MAX_REQUEST_BYTES"))
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

        version_header: str | None = http_request.headers.get(_VERSION_HEADER)
        negotiated: str | None = negotiate_protocol_version(
            version_header, is_initialize=is_initialize
        )
        if negotiated is None:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Missing or unsupported MCP-Protocol-Version",
                request_id=getattr(message, "id", None),
            )
        protocol_version: str = negotiated

        store = self._require_session_store()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        if not is_initialize and (not session_id or not store.exists(session_id)):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Unknown or missing MCP-Session-Id",
                status=404,
                request_id=getattr(message, "id", None),
            )

        backend = self._require_auth_backend()
        token = backend.authenticate(http_request)
        if token is None:
            challenge: str = backend.www_authenticate_challenge(error="invalid_token")
            response = JsonResponse(
                {"error": "unauthorized", "error_description": "Authentication required."},
                status=401,
            )
            response["WWW-Authenticate"] = challenge
            return response

        context = MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._require_tools(),
            resources=self._require_resources(),
            prompts=self._require_prompts(),
            protocol_version=protocol_version,
            session_id=session_id,
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
        http_response = JsonResponse(response_body, status=200)

        if is_initialize and not isinstance(result, JsonRpcError):
            new_session: str = store.create()
            http_response[_SESSION_HEADER] = new_session
        return http_response

    def handle_get(self, request: Request) -> HttpResponse:
        """GET action: SSE-from-server isn't implemented in v1; 405 per spec."""
        http_request = request._request  # noqa: SLF001
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard
        return HttpResponse(status=405)

    def terminate_session(self, request: Request) -> HttpResponse:
        """DELETE action: end the session named by ``MCP-Session-Id``."""
        http_request = request._request  # noqa: SLF001
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        if session_id:
            self._require_session_store().destroy(session_id)
        return HttpResponse(status=204)

    # ----- collaborator accessors -----

    def _check_origin(self, request: Any) -> HttpResponse | None:
        origin: str | None = request.headers.get("Origin")
        if not is_origin_allowed(origin):
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
