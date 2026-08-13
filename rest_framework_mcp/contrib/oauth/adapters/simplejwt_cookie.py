from __future__ import annotations

from typing import Any, cast

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpRequest

from rest_framework_mcp.conf import get_setting


class SimpleJWTCookieAdapter:
    """Reference :class:`AuthUserAdapter` for SimpleJWT cookie-authenticated apps.

    Reads the access-token cookie (``cookie_name=``, defaulting to
    ``REST_FRAMEWORK_MCP['SIMPLEJWT_ACCESS_COOKIE']``), decodes it with
    :class:`rest_framework_simplejwt.tokens.AccessToken` and looks the user up
    by primary key. Every failure mode — no cookie, malformed or expired
    token, unknown user — returns ``None``, so DOT's view falls back to its
    session-based flow.

    ``rest_framework_simplejwt`` is imported lazily inside :meth:`hydrate`, so
    this module stays importable without the ``[jwt]`` extra and a consumer
    who configures the adapter without it gets a clear ``ImportError`` at first
    request rather than at import.
    """

    def __init__(self, *, cookie_name: str | None = None) -> None:
        self._cookie_name: str = (
            cookie_name if cookie_name is not None else get_setting("SIMPLEJWT_ACCESS_COOKIE")
        )

    def hydrate(self, request: HttpRequest) -> AbstractBaseUser | None:
        token_str: str | None = request.COOKIES.get(self._cookie_name)
        if not token_str:
            return None

        try:
            from rest_framework_simplejwt.tokens import (
                AccessToken,
            )
        except ImportError as exc:  # pragma: no cover - exercised by smoke job w/o simplejwt
            raise ImportError(
                "SimpleJWTCookieAdapter requires `djangorestframework-simplejwt`. "
                'Install it via `pip install "djangorestframework-mcp-server[jwt]"` '
                "or pass a different auth_user_adapter= to build_oauth_urlpatterns."
            ) from exc

        try:
            # simplejwt's stub declares ``token: Optional[Token]`` but the
            # runtime accepts a raw string, its documented public surface. Cast
            # through ``Any`` rather than loosening the surrounding code.
            token = AccessToken(cast(Any, token_str))
        except Exception:
            # ``AccessToken`` raises a hierarchy of ``TokenError`` subclasses.
            # All of them mean "no valid user from this cookie"; the detail is
            # in simplejwt's own logs.
            return None

        user_id = token.get("user_id")
        if user_id is None:
            return None

        user_model = get_user_model()
        try:
            return user_model.objects.get(pk=user_id)
        except user_model.DoesNotExist:
            return None


__all__ = ["SimpleJWTCookieAdapter"]
