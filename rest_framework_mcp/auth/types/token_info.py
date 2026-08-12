from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenInfo:
    """Authenticated principal carried alongside an MCP request.

    Backends construct this once per request and attach it to the Django
    ``HttpRequest`` (as ``request.mcp_token``). Permission classes consult it
    to gate tool/resource access.

    Attributes:
        user: The resolved Django user, or an ``AnonymousUser`` equivalent.
            ``Any`` because the user model is project-defined.
        scopes: OAuth scopes proven by the bearer token.
        audience: The ``aud`` claim. RFC 8707 requires it to match the
            canonical ``/mcp`` URL; backends own that comparison.
        raw: Backend-specific opaque payload — the ``AccessToken`` row, the JWT
            claims dict — for advanced use cases.
    """

    user: Any
    scopes: tuple[str, ...] = field(default_factory=tuple)
    audience: str | None = None
    raw: Any = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def has_all_scopes(self, scopes: list[str] | tuple[str, ...]) -> bool:
        return all(s in self.scopes for s in scopes)


__all__ = ["TokenInfo"]
