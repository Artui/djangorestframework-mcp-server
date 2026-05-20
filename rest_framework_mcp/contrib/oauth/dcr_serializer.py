from __future__ import annotations

from typing import Any, cast

from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_mcp.contrib.oauth.types.dynamic_client_registration_request import (
    DynamicClientRegistrationRequest,
)


class DynamicClientRegistrationSerializer(DataclassSerializer):
    """RFC 7591 dynamic client registration request shape.

    Wraps :class:`DynamicClientRegistrationRequest` so the validated
    payload arrives at :class:`DynamicClientRegistrationViewSet` as a typed
    dataclass instance (via ``.save()``). Field overrides below replace
    the dataclass-derived auto-generated fields with shapes that
    actually validate the wire contract:

    - ``redirect_uris`` is required + non-empty + child is a URL.
    - ``client_type`` / ``authorization_grant_type`` are ``ChoiceField``
      with choices sourced from DOT's ``Application`` model constants
      at instance construction. Declaring them here lets us reject
      malformed values with a per-field error before the request ever
      reaches the database. The lazy import keeps this module
      importable without the ``[oauth]`` extra.

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


__all__ = ["DynamicClientRegistrationSerializer"]
