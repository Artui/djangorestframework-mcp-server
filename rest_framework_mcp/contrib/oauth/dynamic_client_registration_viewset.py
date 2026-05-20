from __future__ import annotations

import json
from typing import Any

from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.contrib.oauth.dcr_serializer import DynamicClientRegistrationSerializer
from rest_framework_mcp.contrib.oauth.types.dynamic_client_registration_response import (
    DynamicClientRegistrationResponse,
)


class DynamicClientRegistrationViewSet(ViewSet):
    """RFC 7591 Dynamic Client Registration endpoint.

    Default state is locked down: ``DCR_ENABLED=False`` produces a 403
    on every request. To turn DCR on, set the flag in
    ``REST_FRAMEWORK_MCP`` settings and (recommended) also set
    ``DCR_INITIAL_ACCESS_TOKEN`` to a static bearer that clients must
    present.

    Single-action ViewSet — wired as the ``create`` action (POST) via
    ``DynamicClientRegistrationViewSet.as_view({"post": "create"})``.
    Successful POST returns the RFC 7591 client information response
    (``client_id`` / ``client_secret`` / echoed registration metadata)
    and persists a DOT ``Application``.

    DOT (``oauth2_provider``) is imported lazily inside the action so
    this module remains importable without the ``[oauth]`` extra. A
    request that arrives with DCR enabled but DOT absent surfaces a
    clear ``ImportError`` at first use rather than at server startup.

    DRF's default auth / permission / throttling layers are disabled —
    DCR is gated by the dedicated ``DCR_INITIAL_ACCESS_TOKEN`` setting,
    not by DRF's session/token authenticators. The CSRF / session
    middleware is sidestepped because DRF's ``APIView.dispatch`` (which
    ``ViewSet`` inherits) wraps responses with ``csrf_exempt`` semantics
    when no ``SessionAuthentication`` class is configured.
    """

    authentication_classes: tuple = ()  # noqa: RUF012 — DRF class-level config
    permission_classes = (AllowAny,)
    renderer_classes = (JSONRenderer,)

    def create(self, request: Request) -> Response:
        if not get_setting("DCR_ENABLED"):
            return Response(
                {"error": "invalid_request", "error_description": "DCR is disabled"},
                status=403,
            )

        expected_token: str | None = get_setting("DCR_INITIAL_ACCESS_TOKEN")
        if expected_token is not None:
            presented: str = request.META.get("HTTP_AUTHORIZATION", "")
            if presented != f"Bearer {expected_token}":
                return Response(
                    {
                        "error": "invalid_token",
                        "error_description": "Initial access token missing or invalid",
                    },
                    status=401,
                )

        try:
            payload: Any = json.loads(request.body)
        except json.JSONDecodeError:
            return Response(
                {"error": "invalid_request", "error_description": "Request body is not valid JSON"},
                status=400,
            )

        serializer = DynamicClientRegistrationSerializer(data=payload)
        if not serializer.is_valid():
            return Response(
                {
                    "error": "invalid_client_metadata",
                    "error_description": "Validation failed",
                    "detail": serializer.errors,
                },
                status=400,
            )

        try:
            from oauth2_provider.models import Application  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised by smoke job w/o DOT
            raise ImportError(
                "DynamicClientRegistrationViewSet requires `django-oauth-toolkit`. "
                'Install it via `pip install "djangorestframework-mcp-server[oauth]"`.'
            ) from exc

        # ``DataclassSerializer.save()`` returns the validated payload as
        # a :class:`DynamicClientRegistrationRequest` instance — typed
        # access for every downstream read, no dict-key string typos.
        instance = serializer.save()
        client_type: str = instance.client_type or Application.CLIENT_CONFIDENTIAL
        grant_type: str = instance.authorization_grant_type or Application.GRANT_AUTHORIZATION_CODE
        application = Application.objects.create(
            name=instance.client_name[:255],
            redirect_uris=" ".join(instance.redirect_uris),
            client_type=client_type,
            authorization_grant_type=grant_type,
            skip_authorization=False,
        )

        response = DynamicClientRegistrationResponse(
            client_id=application.client_id,
            client_secret=application.client_secret,
            client_id_issued_at=int(application.created.timestamp()),
            client_name=application.name,
            redirect_uris=list(instance.redirect_uris),
            client_type=client_type,
            authorization_grant_type=grant_type,
            scope=instance.scope or None,
        )
        return Response(response.to_dict(), status=201)


__all__ = ["DynamicClientRegistrationViewSet"]
