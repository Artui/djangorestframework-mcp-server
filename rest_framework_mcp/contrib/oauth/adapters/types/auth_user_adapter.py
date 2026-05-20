from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpRequest


@runtime_checkable
class AuthUserAdapter(Protocol):
    """Hydrate ``request.user`` before DOT's ``AuthorizationView`` dispatches.

    The common production setup is "DRF backend with SimpleJWT cookies on
    the same host". DOT's ``AuthorizationView`` only knows about Django's
    standard session-based ``request.user`` — without an adapter, a
    JWT-authenticated user appears anonymous to the OAuth flow and the
    consent screen gets shown again. The adapter is the seam where the
    consumer's preferred authentication scheme decides which user the
    OAuth flow should attribute the grant to.

    Implementations return:

    - The authenticated :class:`AbstractBaseUser` to set on the request
      before delegating to DOT.
    - ``None`` to leave ``request.user`` untouched — DOT then falls back
      to its own session-based flow (which may redirect to login).

    Adapters MUST be safe to instantiate without arguments —
    settings-driven configuration belongs inside the adapter's own
    module so the dotted-path setting can resolve it directly.
    """

    def hydrate(self, request: HttpRequest) -> AbstractBaseUser | None: ...


__all__ = ["AuthUserAdapter"]
