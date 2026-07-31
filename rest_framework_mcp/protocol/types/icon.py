from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.constants import IconTheme

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"https", "data"})


@dataclass(frozen=True)
class Icon:
    """One entry in a wire type's ``icons`` array.

    Icons are pure display metadata: a client shows them beside a tool,
    resource, prompt or the server itself. This package only *emits* them —
    fetching, sanitising and rendering are the client's problem, and the spec
    puts a long list of MUSTs on the consumer side (scheme allowlists, SSRF
    defence, SVG sanitisation, magic-byte sniffing) precisely because icon
    bytes are untrusted input.

    ``src`` is a URI pointing at the image. ``mime_type`` overrides the type
    the source serves (useful when it serves a generic
    ``application/octet-stream``). ``sizes`` are WxH strings — ``("48x48",)``,
    or ``("any",)`` for a scalable format like SVG. ``theme`` says which
    background the icon was drawn for; omit it when the icon works on both.

    ⚠ **``src`` must be ``https:`` or ``data:``.** That is not a style
    preference: the spec requires clients to *reject* icon URIs on any other
    scheme, so an ``http:`` or ``file:`` icon is not a slightly-worse icon,
    it is an icon that will never render. Rejecting it at registration turns
    a silent no-op into a startup error naming the fix.
    """

    src: str
    mime_type: str | None = None
    sizes: tuple[str, ...] = ()
    theme: IconTheme | None = None

    def __post_init__(self) -> None:
        scheme: str = urlparse(self.src).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ImproperlyConfigured(
                f"Icon src={self.src!r} uses the {scheme or 'relative'!r} scheme. "
                "MCP clients are required to reject icon URIs that are not "
                "https: or data:, so this icon would never be displayed. Serve "
                "the image over HTTPS, or inline it as a data: URI."
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"src": self.src}
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.sizes:
            out["sizes"] = list(self.sizes)
        if self.theme is not None:
            out["theme"] = self.theme.value
        return out


__all__ = ["Icon"]
