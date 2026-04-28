from __future__ import annotations

from django.http import HttpResponse, JsonResponse


def build_insufficient_scope_response(challenge: str) -> HttpResponse:
    """Build a 403 response signalling missing OAuth scope.

    Per RFC 6750, the ``error="insufficient_scope"`` value belongs in the
    ``WWW-Authenticate`` header — that's already baked into ``challenge``.
    """
    response: HttpResponse = JsonResponse(
        {"error": "insufficient_scope", "error_description": "Required scope missing."},
        status=403,
    )
    response["WWW-Authenticate"] = challenge
    return response


__all__ = ["build_insufficient_scope_response"]
