from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UICsp:
    """The network origins an interactive view needs, declared to the host.

    The server **declares**; the host **enforces**. A host builds the iframe's
    Content-Security-Policy from this, so an origin the view talks to but does
    not declare here is blocked at runtime, with nothing in the server logs to
    say so.

    Each field is a sequence of origins (``"https://api.example.com"``) mapping
    onto one CSP directive. An empty one is omitted from the payload, so
    declaring nothing declares nothing — which is not the same as declaring
    "deny all", the host's default anyway.

    Attributes:
        connect_domains: ``fetch`` / ``XMLHttpRequest`` / WebSocket targets.
        resource_domains: Images, stylesheets, scripts, fonts. A view loading
            Django ``{% static %}`` assets must list the static origin here; a
            self-contained single-file template needs nothing.
        frame_domains: Origins the view may itself embed in an iframe.
        base_uri_domains: Permitted values for the document's ``<base>``.
    """

    connect_domains: Sequence[str] = ()
    resource_domains: Sequence[str] = ()
    frame_domains: Sequence[str] = ()
    base_uri_domains: Sequence[str] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the extension's camelCase wire shape, omitting empties."""
        # Serialisation boundary (rule 11): the `_meta` payload is a plain
        # JSON object and every extension key inside it is free-form.
        out: dict[str, Any] = {}
        for key, values in (
            ("connectDomains", self.connect_domains),
            ("resourceDomains", self.resource_domains),
            ("frameDomains", self.frame_domains),
            ("baseUriDomains", self.base_uri_domains),
        ):
            if values:
                out[key] = list(values)
        return out


__all__ = ["UICsp"]
