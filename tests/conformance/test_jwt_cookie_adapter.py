"""End-to-end JWT cookie adapter routing through the conformance URL conf.

The unit suite already covers ``SimpleJWTCookieAdapter.hydrate`` in
isolation. This conformance test proves the adapter wiring survives the
full URL → view → DOT delegation: pass an adapter to
``build_oauth_urlpatterns``, drop a real JWT cookie on the request, and
verify DOT's ``AuthorizationView`` sees the authenticated user.

The OAuth flow proper isn't exercised here — DOT's consent screen
behaviour is out of scope. We patch DOT's ``dispatch`` to capture the
``request.user`` it would see, which is the contract the adapter exists
to satisfy.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.test import override_settings

from rest_framework_mcp.contrib.oauth.adapters.simplejwt_cookie import SimpleJWTCookieAdapter
from tests.conformance.urls import conformance_urlconf


@pytest.fixture
def _mock_dot_dispatch():
    from oauth2_provider.views import AuthorizationView

    captured: dict[str, Any] = {}

    def fake_dispatch(self: Any, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        captured["user"] = request.user
        return HttpResponse("captured")

    with patch.object(AuthorizationView, "dispatch", fake_dispatch):
        yield captured


@pytest.mark.django_db(transaction=True)
def test_jwt_cookie_user_reaches_dot_authorize_view(client, _mock_dot_dispatch) -> None:
    from rest_framework_simplejwt.tokens import AccessToken

    user = get_user_model().objects.create_user(username="alice", password="x")
    token = AccessToken.for_user(user)
    client.cookies["access"] = str(token)

    urlconf = conformance_urlconf(auth_user_adapter=SimpleJWTCookieAdapter())
    with override_settings(ROOT_URLCONF=urlconf):
        response = client.get("/oauth/authorize/")
    assert response.status_code == 200, response.content
    assert _mock_dot_dispatch["user"].pk == user.pk


@pytest.mark.django_db(transaction=True)
def test_adapter_cookie_name_is_configurable_end_to_end(client, _mock_dot_dispatch) -> None:
    """Constructing the adapter directly lets it be configured — which the
    dotted-path contract ("adapters MUST be zero-arg constructible") forbade."""
    from rest_framework_simplejwt.tokens import AccessToken

    user = get_user_model().objects.create_user(username="bob", password="x")
    client.cookies["custom-jwt"] = str(AccessToken.for_user(user))

    urlconf = conformance_urlconf(
        auth_user_adapter=SimpleJWTCookieAdapter(cookie_name="custom-jwt")
    )
    with override_settings(ROOT_URLCONF=urlconf):
        response = client.get("/oauth/authorize/")
    assert response.status_code == 200, response.content
    assert _mock_dot_dispatch["user"].pk == user.pk


# The "no-adapter passthrough is transparent over DOT" case is exercised
# at the unit level in
# ``tests/contrib/oauth/test_build_authorize_passthrough_view.py``. We
# don't re-run it as a conformance test because the conformance Django
# settings carry no ``AuthenticationMiddleware`` — ``request.user``
# isn't pre-populated, so the no-adapter branch would surface as an
# ``AttributeError`` from DOT rather than the "DOT sees unchanged user"
# semantic we're trying to verify. The unit test pins that semantic
# against a stub request with ``user`` already set.
