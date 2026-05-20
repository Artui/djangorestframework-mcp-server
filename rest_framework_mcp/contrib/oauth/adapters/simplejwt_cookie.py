from __future__ import annotations

from typing import Any, cast

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpRequest

from rest_framework_mcp.conf import get_setting


class SimpleJWTCookieAdapter:
    """Reference :class:`AuthUserAdapter` for SimpleJWT cookie-authenticated apps.

    Reads the access-token cookie (name configured via
    ``REST_FRAMEWORK_MCP['SIMPLEJWT_ACCESS_COOKIE']``, default
    ``"access"``), decodes it with
    :class:`rest_framework_simplejwt.tokens.AccessToken`, looks the user
    up by primary key, and returns it. Returns ``None`` for any failure
    mode (no cookie, malformed token, expired token, unknown user) —
    DOT's view then falls back to its session-based flow.

    ``rest_framework_simplejwt`` is imported lazily inside :meth:`hydrate`
    so this module remains importable without the ``[jwt]`` extra. A
    consumer who configures this adapter without the extra installed
    surfaces a clear ``ImportError`` at first request, not at import.
    """

    def hydrate(self, request: HttpRequest) -> AbstractBaseUser | None:
        cookie_name: str = get_setting("SIMPLEJWT_ACCESS_COOKIE")
        token_str: str | None = request.COOKIES.get(cookie_name)
        if not token_str:
            return None

        try:
            from rest_framework_simplejwt.tokens import (
                AccessToken,  # type: ignore[import-not-found]
            )
        except ImportError as exc:  # pragma: no cover - exercised by smoke job w/o simplejwt
            raise ImportError(
                "SimpleJWTCookieAdapter requires `djangorestframework-simplejwt`. "
                'Install it via `pip install "djangorestframework-mcp-server[jwt]"` '
                "or configure a different AUTH_USER_ADAPTER."
            ) from exc

        try:
            # simplejwt's stub declares ``token: Optional[Token]`` but the
            # runtime accepts a raw string (the documented public surface).
            # Cast through ``Any`` to bypass the over-narrow stub without
            # disabling type-checking on the surrounding code.
            token = AccessToken(cast(Any, token_str))
        except Exception:
            # ``AccessToken`` raises a hierarchy of ``TokenError`` subclasses
            # (invalid signature, expired, malformed claims). Treat them all
            # as "no valid user from this cookie" rather than surfacing the
            # internal failure mode — the consumer can read simplejwt's logs
            # if they need the detail.
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
