"""Audience enforcement against DOT's *real* token model, not a fake.

The backend suite drives `authenticate` through a `_FakeToken` carrying a
`resource` attribute. Whether DOT's actual `AccessToken` has one decides
whether that path is real or fiction, so these tests use a genuine
`AccessToken` row.

⭐ **DOT 3.4.0 added `resource`**, and this file is how we found out: the
tripwire below was written predicting exactly that, and it fired on the bump.
Before 3.4.0 the audience path was unsatisfiable in every stock deployment —
enforcement could only work against a swapped `ACCESS_TOKEN_MODEL` or a custom
`audience_getter`. From 3.4.0 the default getter is meaningful out of the box.

⚠ The package supports `django-oauth-toolkit>=2.3`, so both realities are live
and these tests are version-gated rather than rewritten. Whether
`ENFORCE_AUDIENCE` should now *default on* is a separate decision — it would be
breaking for anyone on older DOT.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from django.utils import timezone

from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)

RESOURCE_URL = "https://example.test/mcp/"


def _dot_has_resource_indicators() -> bool:
    """Whether the installed DOT implements RFC 8707 resource indicators.

    Feature-detected on the model rather than version-gated on a string: the
    field is the thing the audience getter actually reads, and a project running
    a swapped ``ACCESS_TOKEN_MODEL`` can have it at any DOT version.
    """
    from oauth2_provider.models import get_access_token_model

    return "resource" in {f.name for f in get_access_token_model()._meta.get_fields()}


def test_the_resource_field_premise_is_pinned_to_reality() -> None:
    """The tripwire that caught DOT 3.4.0, kept pointing at the live fact.

    Its predecessor asserted ``"resource" not in fields`` and said in its own
    docstring that failing would mean DOT had added resource indicators. That is
    exactly what happened, so the assertion now tracks the installed version
    instead of a moment in time.
    """
    from importlib.metadata import version

    installed = tuple(int(part) for part in version("django-oauth-toolkit").split(".")[:2])
    assert _dot_has_resource_indicators() == (installed >= (3, 4)), (
        "DOT's resource-indicator support diverged from the version that "
        "introduced it — re-check what the default audience getter reads."
    )


def _issue_token(scope: str = "read write") -> str:
    from oauth2_provider.models import Application, get_access_token_model

    user = get_user_model().objects.create_user(username="alice")
    application = Application.objects.create(
        name="probe",
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris=RESOURCE_URL,
    )
    get_access_token_model().objects.create(
        user=user,
        application=application,
        token="probe-access-token",
        scope=scope,
        expires=timezone.now() + timedelta(hours=1),
    )
    return "probe-access-token"


def _request(token: str) -> RequestFactory:
    return RequestFactory().post("/mcp/", HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.mark.django_db
def test_a_real_token_authenticates_with_a_resource_url_configured(settings) -> None:
    """The reported failure, reproduced exactly.

    Before the fix, configuring a resource URL — which RFC 9728 effectively
    requires a resource server to publish — rejected every token, because
    enforcement was implied by that URL and DOT records no resource on the
    token. The result was a 401 for every request and hosts reporting the
    server as having no tools.
    """
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": RESOURCE_URL}
    token = _issue_token()

    backend = DjangoOAuthToolkitBackend(resource_url=RESOURCE_URL)
    info = backend.authenticate(_request(token))

    assert info is not None
    assert info.scopes == ("read", "write")
    assert info.user.username == "alice"


@pytest.mark.django_db
def test_metadata_still_advertises_the_resource_it_authenticates_for(settings) -> None:
    """The impossible choice is gone: publish valid metadata *and* authenticate."""
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": RESOURCE_URL}
    token = _issue_token()
    backend = DjangoOAuthToolkitBackend(resource_url=RESOURCE_URL)

    assert backend.protected_resource_metadata().to_dict()["resource"] == RESOURCE_URL
    assert backend.authenticate(_request(token)) is not None


def test_enforcement_without_a_usable_audience_source_refuses_to_start(settings) -> None:
    """Loud at startup rather than a 401 per request.

    An operator who turns enforcement on against stock DOT has built a
    server that rejects everything. That is a configuration error, and the
    only useful place to say so is where the configuration is read.
    """
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": RESOURCE_URL, "ENFORCE_AUDIENCE": True}
    if _dot_has_resource_indicators():
        # DOT >= 3.4: the default getter reads a real field, so enforcement is
        # satisfiable and starting up is the correct behaviour. The guard has
        # not weakened — it still refuses when there is genuinely no source.
        DjangoOAuthToolkitBackend()
        return
    with pytest.raises(ImproperlyConfigured, match="no 'resource' field"):
        DjangoOAuthToolkitBackend()


def test_enforcement_with_no_resource_url_refuses_to_start(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"ENFORCE_AUDIENCE": True}
    with pytest.raises(ImproperlyConfigured, match="nothing for a token's resource to match"):
        DjangoOAuthToolkitBackend()


def test_enforcement_starts_when_the_token_model_carries_a_resource(monkeypatch, settings) -> None:
    """The other supported route: swap `OAUTH2_PROVIDER["ACCESS_TOKEN_MODEL"]`.

    DOT allows substituting the model, so a project that adds a `resource`
    field gets working enforcement with the default getter. Patched rather
    than declared as a real swappable model — the check is only "does the
    model declare the field", and a stub exercises exactly that without a
    migration.
    """

    class _Field:
        name = "resource"

    class _Meta:
        @staticmethod
        def get_fields() -> list[_Field]:
            return [_Field()]

    class _TokenModel:
        __name__ = "ResourceBoundAccessToken"
        _meta = _Meta

    monkeypatch.setattr(
        "oauth2_provider.models.get_access_token_model", lambda: _TokenModel, raising=True
    )
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": RESOURCE_URL, "ENFORCE_AUDIENCE": True}

    backend = DjangoOAuthToolkitBackend()
    assert backend.protected_resource_metadata().to_dict()["resource"] == RESOURCE_URL


def test_enforcement_starts_with_an_explicit_audience_getter(settings) -> None:
    """The supported way to enforce: say where the audience actually lives."""
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": RESOURCE_URL, "ENFORCE_AUDIENCE": True}
    backend = DjangoOAuthToolkitBackend(audience_getter=lambda _token: RESOURCE_URL)
    assert backend.protected_resource_metadata().to_dict()["resource"] == RESOURCE_URL


@pytest.mark.django_db
def test_an_explicit_getter_can_reject(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": RESOURCE_URL, "ENFORCE_AUDIENCE": True}
    token = _issue_token()
    backend = DjangoOAuthToolkitBackend(audience_getter=lambda _token: "https://elsewhere/mcp/")
    assert backend.authenticate(_request(token)) is None


def test_an_empty_resource_url_means_unset_not_enforced_against_empty(settings) -> None:
    """`resource_url=""` used to skip the settings fallbacks *and* enforce.

    It was "not None", so it won the argument check, then no token could
    match the empty string.
    """
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": RESOURCE_URL}
    backend = DjangoOAuthToolkitBackend(resource_url="")
    body = backend.protected_resource_metadata().to_dict()
    assert body["resource"] == ""
    assert "_warning" in body


def test_unconfigured_metadata_says_why_resource_is_empty(settings) -> None:
    """RFC 9728 makes `resource` REQUIRED, so an empty one needs explaining."""
    settings.REST_FRAMEWORK_MCP = {}
    body = DjangoOAuthToolkitBackend().protected_resource_metadata().to_dict()
    assert body["resource"] == ""
    assert "RESOURCE_URL" in body["_warning"]


def test_enforcement_refuses_a_swapped_model_without_a_resource(monkeypatch, settings) -> None:
    """The guard still fires — it just needs a model that lacks the field.

    Stock DOT >= 3.4 ships ``resource``, so this path is no longer reachable
    through the default model. It remains reachable, and worth keeping, for an
    older DOT or a project that swapped ``OAUTH2_PROVIDER['ACCESS_TOKEN_MODEL']``
    for one without it — which is exactly when an operator most needs to be told
    at startup rather than by a 401 on every request.
    """

    class _Field:
        name = "expires"

    class _Meta:
        @staticmethod
        def get_fields() -> list[_Field]:
            return [_Field()]

    class _NoResourceToken:
        __name__ = "_NoResourceToken"
        _meta = _Meta()

    monkeypatch.setattr("oauth2_provider.models.get_access_token_model", lambda: _NoResourceToken)
    settings.REST_FRAMEWORK_MCP = {"RESOURCE_URL": RESOURCE_URL, "ENFORCE_AUDIENCE": True}
    with pytest.raises(ImproperlyConfigured, match="no 'resource' field"):
        DjangoOAuthToolkitBackend()
