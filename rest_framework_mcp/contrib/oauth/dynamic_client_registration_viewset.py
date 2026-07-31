from __future__ import annotations

import json
from typing import Any

from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from rest_framework_mcp.contrib.oauth.dcr_serializer import DynamicClientRegistrationSerializer
from rest_framework_mcp.contrib.oauth.types.dynamic_client_registration_response import (
    DynamicClientRegistrationResponse,
)
from rest_framework_mcp.contrib.oauth.utils import OPENID_SCOPE, resolve_id_token_algorithm


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
    (``client_id`` / echoed registration metadata, plus a plaintext
    ``client_secret`` for confidential clients) and persists a DOT
    ``Application``. A client that registers with
    ``token_endpoint_auth_method: none`` becomes a public client and is
    issued no secret — it authenticates at the token endpoint with PKCE
    alone, which is the only mode Claude's custom connectors can use.

    DOT (``oauth2_provider``) is imported lazily inside the action so
    this module remains importable without the ``[oauth]`` extra. A
    request that arrives with DCR enabled but DOT absent surfaces a
    clear ``ImportError`` at first use rather than at server startup.

    DRF's default auth / permission / throttling layers are disabled —
    DCR is gated by its own ``dcr_enabled`` / ``initial_access_token``
    knobs, not by DRF's session/token authenticators. The CSRF / session
    middleware is sidestepped because DRF's ``APIView.dispatch`` (which
    ``ViewSet`` inherits) wraps responses with ``csrf_exempt`` semantics
    when no ``SessionAuthentication`` class is configured.
    """

    authentication_classes: tuple = ()  # noqa: RUF012 — DRF class-level config
    permission_classes = (AllowAny,)
    renderer_classes = (JSONRenderer,)

    # Supplied by ``build_oauth_urlpatterns`` via ``as_view``, resolved there
    # from settings. Defaults are the *safe* ones: a hand-wired viewset that
    # forgets to pass them refuses registrations rather than opening them.
    dcr_enabled: bool = False
    initial_access_token: str | None = None

    def create(self, request: Request) -> Response:
        if not self.dcr_enabled:
            return Response(
                {"error": "invalid_request", "error_description": "DCR is disabled"},
                status=403,
            )

        expected_token: str | None = self.initial_access_token
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
            from oauth2_provider.generators import (  # type: ignore[import-not-found]
                generate_client_secret,
            )
            from oauth2_provider.models import Application  # type: ignore[import-not-found]
            from oauth2_provider.scopes import (  # type: ignore[import-not-found]
                get_scopes_backend,
            )
            from oauth2_provider.settings import (  # type: ignore[import-not-found]
                oauth2_settings,
            )
        except ImportError as exc:  # pragma: no cover - exercised by smoke job w/o DOT
            raise ImportError(
                "DynamicClientRegistrationViewSet requires `django-oauth-toolkit`. "
                'Install it via `pip install "djangorestframework-mcp-server[oauth]"`.'
            ) from exc

        # ``DataclassSerializer.save()`` returns the validated payload as
        # a :class:`DynamicClientRegistrationRequest` instance — typed
        # access for every downstream read, no dict-key string typos. Its
        # RFC 7591 and DOT spellings are already reconciled and defaulted
        # by the serializer, so there is nothing left to resolve here.
        instance = serializer.save()
        client_type: str = instance.client_type
        grant_type: str = instance.authorization_grant_type
        is_confidential: bool = client_type == Application.CLIENT_CONFIDENTIAL

        # DOT has no per-application scope column — scopes are configured
        # globally and checked against the *authorize* request. Echoing a scope
        # we never registered would tell the client it got something it didn't,
        # and it would only find out one leg later, so check it here instead:
        # the same set DOT's ``validate_scopes`` will use, surfaced as a
        # per-field ``invalid_client_metadata`` while there is still something
        # actionable to say. Checked before ``create`` so a rejection leaves no
        # orphan row.
        requested_scopes: list[str] = instance.scope.split()
        available: set[str] = set(get_scopes_backend().get_available_scopes())
        unsupported: list[str] = [s for s in requested_scopes if s not in available]
        if unsupported:
            return Response(
                {
                    "error": "invalid_client_metadata",
                    "error_description": "Validation failed",
                    "detail": {
                        "scope": [
                            f"This authorization server does not offer {unsupported}. "
                            f"Available: {sorted(available)}."
                        ]
                    },
                },
                status=400,
            )

        # Whether an ID token can ever be signed for this client. Left unset,
        # DOT's ``algorithm`` column defaults to NO_ALGORITHM and
        # ``Application.jwk_key`` raises ``ImproperlyConfigured`` the moment
        # ``openid`` is among the granted scopes — a 500 out of the token
        # endpoint, after the user has already logged in and consented. Same
        # rejected-before-``create`` treatment as ``scope`` above: a
        # registration that cannot be honoured fails here, where RFC 7591
        # §3.2.2 has an error code for it, not one leg later where RFC 6749
        # §5.2's channel is never reached.
        algorithm, algorithm_error = resolve_id_token_algorithm(
            instance.id_token_signed_response_alg,
            is_confidential=is_confidential,
            rsa_key_configured=bool(oauth2_settings.OIDC_RSA_PRIVATE_KEY),
        )
        if algorithm_error is not None:
            return Response(
                {
                    "error": "invalid_client_metadata",
                    "error_description": "Validation failed",
                    "detail": {"id_token_signed_response_alg": [algorithm_error]},
                },
                status=400,
            )

        # The residual case the algorithm resolution alone leaves open: a server
        # that publishes ``openid`` in its scopes but holds no signing key. The
        # scope check above passes it (DOT does offer the scope), no algorithm
        # can be registered, and the ID token then fails exactly as it did
        # before — a 500 at the token endpoint. Refuse it here instead, naming
        # the setting that would make it work. Only a client that *declares*
        # ``openid`` is caught; one that registers bare and requests the scope
        # at authorize still reaches DOT, because nothing about that is visible
        # from this endpoint.
        if OPENID_SCOPE in requested_scopes and not algorithm:
            return Response(
                {
                    "error": "invalid_client_metadata",
                    "error_description": "Validation failed",
                    "detail": {
                        "scope": [
                            f"`{OPENID_SCOPE}` needs an ID token, and this server has no "
                            "signing key: set OAUTH2_PROVIDER['OIDC_RSA_PRIVATE_KEY'], or "
                            "register without that scope."
                        ]
                    },
                },
                status=400,
            )

        # Generate the secret here rather than letting the model default fire,
        # because ``ClientSecretField.pre_save`` hashes the column in place:
        # after ``create()`` the attribute holds a PBKDF2 digest, and returning
        # that is the same as issuing a credential nobody can authenticate
        # with. This is the only moment the plaintext exists. Public clients
        # still get a stored secret (so the row can never be authenticated
        # against a known value) but are never handed one — RFC 7591 §2 issues
        # secrets only to clients that authenticate.
        client_secret: str = generate_client_secret()
        application = Application.objects.create(
            name=instance.client_name[:255],
            redirect_uris=" ".join(instance.redirect_uris),
            client_type=client_type,
            authorization_grant_type=grant_type,
            client_secret=client_secret,
            algorithm=algorithm,
            skip_authorization=False,
        )

        response = DynamicClientRegistrationResponse(
            client_id=application.client_id,
            client_secret=client_secret if is_confidential else None,
            client_id_issued_at=int(application.created.timestamp()),
            client_name=application.name,
            # Read back off the row, not the request: RFC 7591 §3.2.1 asks for
            # what was registered, and ``client_name`` in particular may have
            # been truncated on the way in.
            redirect_uris=application.redirect_uris.split(),
            grant_types=list(instance.grant_types),
            response_types=list(instance.response_types),
            token_endpoint_auth_method=instance.token_endpoint_auth_method,
            id_token_signed_response_alg=application.algorithm,
            # Nothing persisted it — DOT has no column — so this is the
            # request's value, unlike every other field here.
            application_type=instance.application_type,
            client_type=client_type,
            authorization_grant_type=grant_type,
            scope=instance.scope or None,
        )
        return Response(response.to_dict(), status=201)


__all__ = ["DynamicClientRegistrationViewSet"]
