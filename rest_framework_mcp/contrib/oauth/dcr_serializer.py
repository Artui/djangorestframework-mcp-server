from __future__ import annotations

import re
from typing import Any, cast
from urllib.parse import urlsplit

from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_mcp.contrib.oauth.types.dynamic_client_registration_request import (
    DynamicClientRegistrationRequest,
)

# RFC 7591 §2 ``token_endpoint_auth_method`` values, mapped to the
# ``Application.CLIENT_*`` value each implies. Must stay in lockstep with
# ``AuthorizationServerMetadata.token_endpoint_auth_methods_supported``: a
# method advertised but not registerable is the failure this prevents.
#
# The DOT constants are spelled literally, not imported, so this module stays
# importable without the ``[oauth]`` extra; the tests pin them against
# ``Application`` so drift fails the suite instead of production.
_AUTH_METHOD_CLIENT_TYPES = {
    "client_secret_basic": "confidential",
    "client_secret_post": "confidential",
    "none": "public",
}

# RFC 7591 §2 ``grant_types``, mapped to the ``Application.GRANT_*`` value DOT
# stores in its single-valued ``authorization_grant_type`` column.
# ``refresh_token`` is absent because it is a modifier rather than a primary
# grant — DOT issues refresh tokens off whichever grant is configured — so it
# is filtered out before the remaining entry is resolved.
_GRANT_TYPE_ALIASES = {
    "authorization_code": "authorization-code",
    "client_credentials": "client-credentials",
    "implicit": "implicit",
    "password": "password",
}
_GRANT_TYPE_RFC_NAMES = {dot: rfc for rfc, dot in _GRANT_TYPE_ALIASES.items()}
_REFRESH_TOKEN_GRANT = "refresh_token"

# Of the grants RFC 7591 can name, the ones this endpoint will actually
# register. The rest stay in the alias map above so a caller asking for one is
# told *why* it is refused rather than that it is unspelt.
#
# **Authorization code only, and deliberately.** A dynamic registration creates
# a client with no owning user and no human in the loop, so the grant it hands
# out must be one that cannot mint a token without one. ``client_credentials``
# can: the resulting token carries no user at all and still satisfies every
# scope-gated tool, so a registration endpoint left open becomes an
# unauthenticated path onto the whole tool surface. ``password`` and
# ``implicit`` are removed from OAuth 2.1 outright. The set also matches what
# ``AuthorizationServerMetadata.grant_types_supported`` advertises, so
# registration no longer accepts a grant this server never claimed to offer.
_REGISTERABLE_GRANT_TYPES = frozenset({"authorization_code"})

# RFC 7591 §2.1: ``response_types`` is a function of the grant, and has no DOT
# column, so it is derived from the resolved grant and echoed, never stored.
# The client-credentials and password grants never reach the authorization
# endpoint, hence the empty lists. Kept complete even though only the
# authorization-code row is reachable through ``_REGISTERABLE_GRANT_TYPES``:
# this is the RFC's mapping, not a policy, and it is also what makes the field's
# accepted vocabulary wide enough to tell a caller its ``response_types``
# contradict its grant instead of merely misspelling one.
_GRANT_RESPONSE_TYPES = {
    "authorization-code": ["code"],
    "implicit": ["token"],
    "client-credentials": [],
    "password": [],
}
_RESPONSE_TYPES = sorted({rt for types in _GRANT_RESPONSE_TYPES.values() for rt in types})

# RFC 7591 §2 / OIDC Core §2 ``id_token_signed_response_alg``, restricted to
# what DOT can sign with. OIDC's ``none`` is absent: ``jwk_key`` raises for
# anything but RS256 or HS256, so accepting it would register a client whose
# first ID token is a 500. Whether a listed algorithm is *usable* depends on
# server configuration and client type, and is resolved in the viewset.
_ID_TOKEN_ALGORITHMS = ["HS256", "RS256"]

# OIDC Dynamic Client Registration 1.0 ``application_type``. Validated and
# echoed but **not** enforced: this server is not an OIDC provider, DOT has no
# column for it, and rejecting a ``web`` client with a localhost redirect URI
# would invent a restriction the underlying authorization server never applies.
_APPLICATION_TYPES = ["native", "web"]

# RFC 3986 §3.1 scheme syntax. A redirect URI must be absolute — RFC 6749 §3.1.2
# — and this is what makes it so; ``urlsplit`` alone is content with a relative
# reference.
_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*$")

# DOT's own ``ALLOWED_REDIRECT_URI_SCHEMES`` default, spelled literally like the
# other DOT constants here so the module stays importable without the ``[oauth]``
# extra. It stands in only when DOT is absent, where nothing can be registered
# anyway.
_DEFAULT_REDIRECT_URI_SCHEMES = frozenset({"http", "https"})


def _allowed_redirect_uri_schemes() -> frozenset[str]:
    """The redirect-URI schemes the underlying authorization server will honour."""
    try:
        from oauth2_provider.settings import oauth2_settings
    except ImportError:  # pragma: no cover - exercised by smoke job w/o DOT
        return _DEFAULT_REDIRECT_URI_SCHEMES
    return frozenset(scheme.lower() for scheme in oauth2_settings.ALLOWED_REDIRECT_URI_SCHEMES)


# What a registration naming neither vocabulary gets, and — in reverse — the
# RFC method echoed back to a caller who spelled its intent DOT's way. RFC 7591
# §2 defaults an omitted method to ``client_secret_basic``.
_DEFAULT_AUTH_METHOD = "client_secret_basic"
_DEFAULT_GRANT_TYPE = "authorization_code"
_CLIENT_TYPE_AUTH_METHODS = {
    "confidential": _DEFAULT_AUTH_METHOD,
    "public": "none",
}


class DynamicClientRegistrationSerializer(DataclassSerializer):
    """RFC 7591 dynamic client registration request shape.

    Wraps
    [`DynamicClientRegistrationRequest`][rest_framework_mcp.contrib.oauth.types.dynamic_client_registration_request.DynamicClientRegistrationRequest]
    so ``.save()`` hands
    [`DynamicClientRegistrationViewSet`][rest_framework_mcp.contrib.oauth.dynamic_client_registration_viewset.DynamicClientRegistrationViewSet]
    a typed dataclass instance. The field overrides replace the dataclass-derived
    defaults with shapes that validate the wire contract: ``redirect_uris`` is required,
    non-empty and absolute-URI-valued, and ``application_type`` is checked against
    OIDC's two values and echoed without imposing the redirect-URI constraints an OIDC
    provider would derive from it.

    ``token_endpoint_auth_method`` / ``grant_types`` are the RFC 7591 §2
    spellings every interoperable client sends, and are the *primary* inputs:
    ``validate`` translates them into DOT's ``client_type`` /
    ``authorization_grant_type``. Those two remain accepted as an escape hatch
    for callers already speaking DOT, their choices sourced from
    ``Application``'s constants at instance construction — so a malformed value
    is rejected per-field before reaching the database, and the lazy import
    keeps this module usable without the ``[oauth]`` extra.

    Supplying both spellings is allowed only when they agree. A contradiction
    is a 400 rather than a silent winner, since either choice would hand back a
    client that cannot complete the flow it registered for. Other RFC 7591
    fields are ignored: DOT does not model them, and inventing a richer shape
    would diverge from the underlying authorization server.

    ``authorization_code`` is the only primary grant registerable here, in
    either vocabulary — see ``_REGISTERABLE_GRANT_TYPES``. ``refresh_token`` may
    ride along.
    """

    class Meta:
        dataclass = DynamicClientRegistrationRequest

    redirect_uris = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=True,
        allow_empty=False,
        help_text=(
            "Per RFC 7591 §2, one or more absolute redirect URIs are required. The "
            "accepted schemes are whichever the underlying authorization server "
            "accepts (DOT's `ALLOWED_REDIRECT_URI_SCHEMES`), so a native client's "
            "private-use scheme registers wherever it would also be honoured."
        ),
    )
    client_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    scope = serializers.CharField(required=False, allow_blank=True)
    token_endpoint_auth_method = serializers.ChoiceField(
        choices=sorted(_AUTH_METHOD_CLIENT_TYPES),
        required=False,
        help_text="RFC 7591 §2. `none` registers a public (PKCE-only) client.",
    )
    grant_types = serializers.ListField(
        child=serializers.ChoiceField(choices=[*sorted(_GRANT_TYPE_ALIASES), _REFRESH_TOKEN_GRANT]),
        required=False,
        allow_empty=False,
        help_text=(
            "RFC 7591 §2. DOT models one primary grant, so at most one non-refresh "
            "entry, and `authorization_code` is the only one this endpoint registers."
        ),
    )
    response_types = serializers.ListField(
        child=serializers.ChoiceField(choices=_RESPONSE_TYPES),
        required=False,
        allow_empty=True,
        help_text="RFC 7591 §2.1. Derived from the grant; supply it only to assert the same.",
    )
    id_token_signed_response_alg = serializers.ChoiceField(
        choices=_ID_TOKEN_ALGORITHMS,
        required=False,
        help_text="RFC 7591 §2. Omit to take the strongest algorithm this server can sign with.",
    )
    application_type = serializers.ChoiceField(
        choices=_APPLICATION_TYPES,
        required=False,
        help_text=(
            "OIDC Registration 1.0. `native` for desktop / CLI / localhost clients, "
            "`web` for a remotely hosted one. Echoed back; not enforced here."
        ),
    )
    # Choices are populated in ``__init__`` so this module imports without DOT.
    client_type = serializers.ChoiceField(choices=[], required=False)
    authorization_grant_type = serializers.ChoiceField(choices=[], required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # DOT's model constants are read only when an instance is constructed,
        # i.e. on a real request, keeping the module importable without the
        # ``[oauth]`` extra.
        try:
            from oauth2_provider.models import Application
        except ImportError:  # pragma: no cover - exercised by smoke job w/o DOT
            return
        # ``self.fields[...]`` is typed as DRF ``Field``; the concrete class
        # here is ``ChoiceField``, which carries ``choices``. ty cannot narrow
        # a dict-indexed lookup, hence the cast.
        cast(Any, self.fields["client_type"]).choices = [
            (Application.CLIENT_CONFIDENTIAL, Application.CLIENT_CONFIDENTIAL),
            (Application.CLIENT_PUBLIC, Application.CLIENT_PUBLIC),
        ]
        # Narrowed to the same single grant ``_REGISTERABLE_GRANT_TYPES`` admits:
        # the DOT spelling is an escape hatch for callers who already speak DOT,
        # not a second door onto the grants the RFC spelling refuses.
        cast(Any, self.fields["authorization_grant_type"]).choices = [
            (Application.GRANT_AUTHORIZATION_CODE, Application.GRANT_AUTHORIZATION_CODE),
        ]

    def validate_redirect_uris(self, value: list[str]) -> list[str]:
        """Require absolute URIs whose scheme the authorization server will honour.

        DRF's ``URLField`` was the wrong instrument: its ``URLValidator``
        allowlists the http family, so it refused exactly the private-use
        schemes (``com.example.app:/oauth2redirect``) that RFC 8252 §7.1 defines
        for the native clients this serializer's own ``application_type`` exists
        to describe, while admitting ``ftp``, which no OAuth client redirects to.

        The authority on which schemes are acceptable is the authorization
        server that will later match the redirect, not this endpoint: DOT
        publishes it as ``ALLOWED_REDIRECT_URI_SCHEMES``, defaulting to the http
        family and widened by the operator who deploys native clients. Checking
        the same list here means a registration is refused only where the
        authorization request would have been refused anyway — with an
        actionable 400 at registration rather than a dead end mid-flow — and
        never invents a restriction DOT does not apply.
        """
        allowed: frozenset[str] = _allowed_redirect_uri_schemes()
        for uri in value:
            try:
                scheme: str = urlsplit(uri).scheme
            except ValueError:
                # ``urlsplit`` raises on a malformed authority (an unclosed IPv6
                # literal); unhandled it would leave the endpoint with a 500.
                scheme = ""
            if not _URI_SCHEME_RE.match(scheme) or uri[len(scheme) + 1 :] == "":
                raise serializers.ValidationError(
                    f"{uri!r} is not an absolute URI. RFC 6749 §3.1.2 requires a "
                    "redirect URI with a scheme, e.g. `https://app.example/cb` or "
                    "`com.example.app:/oauth2redirect`."
                )
            if scheme.lower() not in allowed:
                raise serializers.ValidationError(
                    f"This authorization server does not redirect to `{scheme}:` URIs. "
                    f"Allowed: {sorted(allowed)}. A native client's private-use scheme "
                    "needs the operator to add it to "
                    "OAUTH2_PROVIDER['ALLOWED_REDIRECT_URI_SCHEMES']."
                )
        return value

    def validate(self, attrs: DynamicClientRegistrationRequest) -> DynamicClientRegistrationRequest:
        """Reconcile the RFC 7591 and DOT spellings, in both directions.

        Downstream reads all four fields already populated and mutually
        consistent, so nobody has to know which vocabulary the client used and
        nobody re-applies defaults.

        ``attrs`` is the dataclass ``DataclassSerializer.to_internal_value``
        built, mutable precisely so normalisation can happen in place. Omitted
        fields carry the dataclass defaults (``""`` / ``[]``), neither of which
        is an accepted choice, so "empty" unambiguously means "not supplied".
        """
        if attrs.token_endpoint_auth_method:
            self._apply(
                attrs,
                "client_type",
                _AUTH_METHOD_CLIENT_TYPES[attrs.token_endpoint_auth_method],
                source="token_endpoint_auth_method",
            )
        else:
            attrs.client_type = attrs.client_type or _AUTH_METHOD_CLIENT_TYPES[_DEFAULT_AUTH_METHOD]
            attrs.token_endpoint_auth_method = _CLIENT_TYPE_AUTH_METHODS[attrs.client_type]

        if attrs.grant_types:
            self._apply(
                attrs,
                "authorization_grant_type",
                self._resolve_grant(attrs.grant_types),
                source="grant_types",
            )
        else:
            attrs.authorization_grant_type = (
                attrs.authorization_grant_type or _GRANT_TYPE_ALIASES[_DEFAULT_GRANT_TYPE]
            )
            attrs.grant_types = [_GRANT_TYPE_RFC_NAMES[attrs.authorization_grant_type]]

        # Derived last, because it is a function of the grant just resolved.
        derived: list[str] = _GRANT_RESPONSE_TYPES[attrs.authorization_grant_type]
        if attrs.response_types and set(attrs.response_types) != set(derived):
            raise serializers.ValidationError(
                {
                    "response_types": [
                        f"The `{attrs.authorization_grant_type}` grant uses {derived}. "
                        "Supply the matching response types, or omit the field."
                    ]
                }
            )
        attrs.response_types = list(derived)
        return attrs

    @staticmethod
    def _resolve_grant(grant_types: list[str]) -> str:
        """Pick the single DOT grant implied by an RFC ``grant_types`` list."""
        primary = {g for g in grant_types if g != _REFRESH_TOKEN_GRANT}
        if not primary:
            raise serializers.ValidationError(
                {
                    "grant_types": [
                        f"`{_REFRESH_TOKEN_GRANT}` is not a standalone grant; "
                        "name the primary grant as well."
                    ]
                }
            )
        if len(primary) > 1:
            raise serializers.ValidationError(
                {
                    "grant_types": [
                        "This authorization server registers one primary grant per client; "
                        f"got {sorted(primary)}."
                    ]
                }
            )
        grant: str = primary.pop()
        if grant not in _REGISTERABLE_GRANT_TYPES:
            raise serializers.ValidationError(
                {
                    "grant_types": [
                        f"`{grant}` cannot be registered dynamically. A dynamically "
                        "registered client has no owning user, so it may only hold a "
                        "grant that requires one at every token request: "
                        f"{sorted(_REGISTERABLE_GRANT_TYPES)}."
                    ]
                }
            )
        return _GRANT_TYPE_ALIASES[grant]

    @staticmethod
    def _apply(
        attrs: DynamicClientRegistrationRequest, target: str, value: str, *, source: str
    ) -> None:
        """Write ``value`` onto ``attrs.<target>``, rejecting a contradiction."""
        existing: str = getattr(attrs, target)
        if existing and existing != value:
            raise serializers.ValidationError(
                {
                    target: [
                        f"Contradicts `{source}`, which implies `{value}`. "
                        "Supply one spelling, or make them agree."
                    ]
                }
            )
        setattr(attrs, target, value)


__all__ = ["DynamicClientRegistrationSerializer"]
