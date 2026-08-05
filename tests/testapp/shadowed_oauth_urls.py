"""A URLconf where another app answers the contested OAuth discovery paths.

Reproduces the DOT 3.4.0 collision: both packages serve ``register/`` and the
authorization-server well-known, and Django resolves first-match, so mounting
the other app first silently wins.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.urls import path


def _not_ours(_request):  # noqa: ANN001, ANN202 - a stand-in for oauth2_provider
    return HttpResponse("{}", content_type="application/json")


urlpatterns = [
    path("oauth/register/", _not_ours),
    path(".well-known/oauth-authorization-server", _not_ours),
]
