"""End-to-end RFC 7591 DCR lifecycle via the conformance URL conf.

Drives Django's URL resolver against ``/oauth/register/`` and verifies the
disabled-by-default lockdown plus the happy-path that persists a DOT
``Application``. Goes through the actual URL conf (not direct view calls) so any
reverse-resolution wiring drift gets caught here.

The gates are resolved when ``build_oauth_urlpatterns`` runs, so each test mounts
a URL conf built with the gates it wants rather than mutating settings.
"""

from __future__ import annotations

import json

import pytest
from django.test import override_settings

from tests.conformance.urls import conformance_urlconf


def test_register_endpoint_disabled_by_default_returns_403(client) -> None:
    with override_settings(ROOT_URLCONF=conformance_urlconf(dcr_enabled=False)):
        response = client.post(
            "/oauth/register/",
            data=json.dumps({"redirect_uris": ["https://c.example/cb"]}),
            content_type="application/json",
        )
    assert response.status_code == 403


@pytest.mark.django_db(transaction=True)
def test_dcr_happy_path_creates_dot_application(client) -> None:
    with override_settings(ROOT_URLCONF=conformance_urlconf(dcr_enabled=True)):
        response = client.post(
            "/oauth/register/",
            data=json.dumps(
                {
                    "redirect_uris": ["https://c.example/cb"],
                    "client_name": "Conformance client",
                }
            ),
            content_type="application/json",
        )
    assert response.status_code == 201, response.content
    body = json.loads(response.content)
    assert body["client_id"]
    assert body["client_secret"]
    assert body["client_name"] == "Conformance client"

    from oauth2_provider.models import Application

    assert Application.objects.filter(client_id=body["client_id"]).exists()


@pytest.mark.django_db(transaction=True)
def test_dcr_initial_access_token_gate(client) -> None:
    urlconf = conformance_urlconf(dcr_enabled=True, dcr_initial_access_token="secret")
    with override_settings(ROOT_URLCONF=urlconf):
        # Without the token: 401.
        no_token = client.post(
            "/oauth/register/",
            data=json.dumps({"redirect_uris": ["https://c.example/cb"]}),
            content_type="application/json",
        )
        assert no_token.status_code == 401

        # With the right token: 201.
        with_token = client.post(
            "/oauth/register/",
            data=json.dumps({"redirect_uris": ["https://c.example/cb"]}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer secret",
        )
    assert with_token.status_code == 201


@pytest.mark.django_db(transaction=True)
def test_two_mounts_can_gate_dcr_differently(client) -> None:
    """The payoff of resolving at build time: one project, two policies."""
    body = json.dumps({"redirect_uris": ["https://c.example/cb"]})

    with override_settings(ROOT_URLCONF=conformance_urlconf(dcr_enabled=True)):
        open_mount = client.post("/oauth/register/", data=body, content_type="application/json")
    with override_settings(ROOT_URLCONF=conformance_urlconf(dcr_enabled=False)):
        closed_mount = client.post("/oauth/register/", data=body, content_type="application/json")

    assert open_mount.status_code == 201
    assert closed_mount.status_code == 403
