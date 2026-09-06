"""A URLconf where another app answers the contested OAuth discovery paths.

Reproduces the DOT 3.4.0 collision: both packages serve ``register/`` and the
authorization-server well-known, and Django resolves first-match, so mounting
the other app first silently wins.

The metadata routes below are shaped like DOT's own ``metadata_urlpatterns``,
including the ``<path:...>`` component forms, and are mounted **at the server
root** because that is what DOT's documentation tells deployers to do for RFC
8414 and RFC 9728 discovery. That arrangement takes the protected-resource
document this package serves, which is the half the check used to miss: a
client that reads it is told the wrong ``resource`` and asks for a token minted
for something else.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.urls import path, re_path


def _not_ours(_request, **_kwargs):  # noqa: ANN001, ANN003, ANN202 - stands in for oauth2_provider
    return HttpResponse("{}", content_type="application/json")


urlpatterns = [
    path("oauth/register/", _not_ours),
    # Served so the "not reported" assertion is about the contested set leaving
    # it out, not about the path resolving nowhere -- which would pass with the
    # entry present and prove nothing.
    path("oauth/authorize/", _not_ours),
    path(".well-known/oauth-authorization-server", _not_ours),
    path(".well-known/oauth-authorization-server/<path:issuer_path>", _not_ours),
    path(".well-known/oauth-protected-resource", _not_ours),
    path(".well-known/oauth-protected-resource/<path:resource_path>", _not_ours),
    re_path(r"^\.well-known/openid-configuration/?$", _not_ours),
]
