from __future__ import annotations

from typing import Any, cast

from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_mcp.contrib.oauth.types.dynamic_client_registration_request import (
    DynamicClientRegistrationRequest,
)

# RFC 7591 §2 ``token_endpoint_auth_method`` values we accept, mapped to the
# ``Application.CLIENT_*`` value each one implies. Kept in lockstep with
# ``AuthorizationServerMetadata.token_endpoint_auth_methods_supported`` — a
# method we advertise but cannot register is exactly the failure this mapping
# exists to prevent.
#
# The DOT constants are spelled literally rather than imported: this module has
# to stay importable without the ``[oauth]`` extra, and these strings are the
# values DOT persists in the ``client_type`` column, so they are a stable
# contract rather than an implementation detail. ``test_dcr_serializer`` pins
# them against ``Application`` so drift fails the suite instead of production.
_AUTH_METHOD_CLIENT_TYPES = {
    "client_secret_basic": "confidential",
    "client_secret_post": "confidential",
    "none": "public",
}

# RFC 7591 §2 ``grant_types`` values, mapped to the ``Application.GRANT_*``
# value DOT stores in its single-valued ``authorization_grant_type`` column
# (same literal-vs-import reasoning as above). ``refresh_token`` is
# deliberately absent: it is a modifier rather than a primary grant — DOT
# issues refresh tokens off the back of whichever grant is configured — so it
# is filtered out before the remaining entry is resolved.
_GRANT_TYPE_ALIASES = {
    "authorization_code": "authorization-code",
    "client_credentials": "client-credentials",
    "implicit": "implicit",
    "password": "password",
}
_GRANT_TYPE_RFC_NAMES = {dot: rfc for rfc, dot in _GRANT_TYPE_ALIASES.items()}
_REFRESH_TOKEN_GRANT = "refresh_token"

# RFC 7591 §2.1: ``response_types`` is a function of the grant, not an
# independent choice. It has no DOT column, so it is derived from the resolved
# grant and echoed — never stored. The client-credentials and password grants
# use the token endpoint directly and reach the authorization endpoint not at
# all, hence the empty lists.
_GRANT_RESPONSE_TYPES = {
    "authorization-code": ["code"],
    "implicit": ["token"],
    "client-credentials": [],
    "password": [],
}
_RESPONSE_TYPES = sorted({rt for types in _GRANT_RESPONSE_TYPES.values() for rt in types})

# RFC 7591 §2 / OIDC Core §2 ``id_token_signed_response_alg``, restricted to the
# ``Application.ALGORITHM_TYPES`` DOT can actually sign with. OIDC's ``none``
# (an unsigned ID token) is deliberately absent: DOT's ``jwk_key`` raises for
# anything that is not RS256 or HS256, so accepting ``none`` would register a
# client whose very first ID token is a 500 — the failure this list exists to
# stop. Whether a listed algorithm is *usable* is a server-configuration and
# client-type question, resolved in the viewset.
_ID_TOKEN_ALGORITHMS = ["HS256", "RS256"]

# OpenID Connect Dynamic Client Registration 1.0 ``application_type``. The MCP
# spec makes *sending* this a client MUST — an OIDC authorization server
# derives redirect-URI constraints from it, and an omitted value defaults to
# ``web``, which conflicts with the ``localhost`` redirect URIs a desktop or
# CLI client needs.
#
# ⚠ This server validates and echoes the value but does **not** enforce those
# constraints, because it is not acting as an OIDC provider: the spec says
# non-OIDC servers safely ignore the parameter, and DOT has no column for it.
# Rejecting a ``web`` client with a localhost redirect URI here would invent a
# restriction the underlying authorization server does not apply.
_APPLICATION_TYPES = ["native", "web"]

# What a registration that named neither vocabulary gets, and — for the
# reverse direction — the RFC method to echo back to a caller who spelled its
# intent DOT's way. RFC 7591 §2 defaults an omitted method to
# ``client_secret_basic``; a DOT-spelled public registration means ``none``.
_DEFAULT_AUTH_METHOD = "client_secret_basic"
_DEFAULT_GRANT_TYPE = "authorization_code"
_CLIENT_TYPE_AUTH_METHODS = {
    "confidential": _DEFAULT_AUTH_METHOD,
    "public": "none",
}


class DynamicClientRegistrationSerializer(DataclassSerializer):
    """RFC 7591 dynamic client registration request shape.

    Wraps :class:`DynamicClientRegistrationRequest` so the validated
    payload arrives at :class:`DynamicClientRegistrationViewSet` as a typed
    dataclass instance (via ``.save()``). Field overrides below replace
    the dataclass-derived auto-generated fields with shapes that
    actually validate the wire contract:

    - ``redirect_uris`` is required + non-empty + child is a URL.
    - ``application_type`` is validated against OIDC's two values and
      echoed. Clients are required by the MCP spec to send it, so it is
      accepted rather than dropped — but this server imposes none of the
      redirect-URI constraints an OIDC provider would derive from it.
    - ``token_endpoint_auth_method`` / ``grant_types`` are the RFC 7591
      §2 spellings every interoperable client sends. They are the
      *primary* inputs: :meth:`validate` translates them into DOT's
      ``client_type`` / ``authorization_grant_type``.
    - ``client_type`` / ``authorization_grant_type`` are DOT's
      non-standard spellings, retained as an escape hatch for callers
      already speaking DOT. They are ``ChoiceField`` with choices sourced
      from DOT's ``Application`` model constants at instance
      construction, so a malformed value is rejected per-field before the
      request ever reaches the database. The lazy import keeps this
      module importable without the ``[oauth]`` extra.

    Supplying both spellings is allowed only when they agree; a
    contradiction is a 400 rather than a silent winner, because either
    choice would hand back a client that cannot complete the flow it
    registered for.

    Other RFC 7591 fields are silently ignored — DOT doesn't model them
    and inventing a richer shape would diverge from the underlying
    authorization server.
    """

    class Meta:
        dataclass = DynamicClientRegistrationRequest

    redirect_uris = serializers.ListField(
        child=serializers.URLField(),
        required=True,
        allow_empty=False,
        help_text="Per RFC 7591 §2, one or more redirect URIs are required.",
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
        help_text="RFC 7591 §2. DOT models one primary grant, so at most one non-refresh entry.",
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
    # Choices populated dynamically in ``__init__`` so test environments
    # without DOT installed don't break import of this module.
    client_type = serializers.ChoiceField(choices=[], required=False)
    authorization_grant_type = serializers.ChoiceField(choices=[], required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Lazy: only inspect DOT model constants when an instance is
        # constructed (i.e. on a real request). Keeps the module
        # importable without the ``[oauth]`` extra installed.
        try:
            from oauth2_provider.models import Application  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover - exercised by smoke job w/o DOT
            return
        # ``self.fields[...]`` returns DRF ``Field``; the concrete subclass
        # here is ``ChoiceField`` which carries a ``choices`` attribute. ty
        # can't narrow the dict-indexed lookup, so cast through ``Any``.
        cast(Any, self.fields["client_type"]).choices = [
            (Application.CLIENT_CONFIDENTIAL, Application.CLIENT_CONFIDENTIAL),
            (Application.CLIENT_PUBLIC, Application.CLIENT_PUBLIC),
        ]
        cast(Any, self.fields["authorization_grant_type"]).choices = [
            (g, g)
            for g in (
                Application.GRANT_AUTHORIZATION_CODE,
                Application.GRANT_CLIENT_CREDENTIALS,
                Application.GRANT_PASSWORD,
                Application.GRANT_IMPLICIT,
            )
        ]

    def validate(self, attrs: DynamicClientRegistrationRequest) -> DynamicClientRegistrationRequest:
        """Reconcile the RFC 7591 and DOT spellings, in both directions.

        Every caller downstream reads all four fields already populated
        and mutually consistent, so nobody has to know which vocabulary
        the client used, and nobody re-applies defaults.

        ``attrs`` is the dataclass instance built by
        ``DataclassSerializer.to_internal_value``; it is mutable precisely
        so normalisation like this can happen in place. Fields the client
        omitted carry the dataclass defaults (``""`` / ``[]``), neither of
        which is an accepted choice, so "empty" unambiguously means "not
        supplied".
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
        return _GRANT_TYPE_ALIASES[primary.pop()]

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
