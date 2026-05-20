"""End-to-end RFC 7591 DCR lifecycle via the conformance URL conf.

Drives Django's URL resolver against ``/oauth/register/`` and verifies
the disabled-by-default lockdown plus the happy-path that persists a
DOT ``Application``. Goes through the actual URL conf (not direct view
calls) so any reverse-resolution wiring drift gets caught here.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.urls("tests.conformance.urls")


def test_register_endpoint_disabled_by_default_returns_403(client, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}  # DCR_ENABLED defaults to False
    response = client.post(
        "/oauth/register/",
        data=json.dumps({"redirect_uris": ["https://c.example/cb"]}),
        content_type="application/json",
    )
    assert response.status_code == 403


@pytest.mark.django_db(transaction=True)
def test_dcr_happy_path_creates_dot_application(client, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"DCR_ENABLED": True}
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
def test_dcr_initial_access_token_gate(client, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "DCR_ENABLED": True,
        "DCR_INITIAL_ACCESS_TOKEN": "secret",
    }
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
