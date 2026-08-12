from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpRequest


@runtime_checkable
class AuthUserAdapter(Protocol):
    """Hydrate ``request.user`` before DOT's ``AuthorizationView`` dispatches.

    DOT's ``AuthorizationView`` knows only Django's session-based
    ``request.user``, so on the common "DRF backend with SimpleJWT cookies"
    setup an authenticated user appears anonymous to the OAuth flow and is
    shown the consent screen again. The adapter is the seam where the
    consumer's own authentication scheme decides which user the flow should
    attribute the grant to.

    :meth:`hydrate` returns the authenticated user to set on the request before
    delegating to DOT, or ``None`` to leave ``request.user`` untouched — DOT
    then falls back to its session-based flow, which may redirect to login.

    Implementations MUST be safe to instantiate without arguments.
    """

    def hydrate(self, request: HttpRequest) -> AbstractBaseUser | None: ...


__all__ = ["AuthUserAdapter"]
