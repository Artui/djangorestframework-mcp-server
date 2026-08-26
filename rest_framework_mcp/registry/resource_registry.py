from __future__ import annotations

import re

from rest_framework_mcp.registry.types.resource_binding import ResourceBinding

_TEMPLATE_VAR = re.compile(r"\{([^}]+)\}")


def _template_to_pattern(uri_template: str) -> re.Pattern[str]:
    """Compile an RFC 6570 (subset) URI template to a regex.

    Simple ``{var}`` placeholders only — enough for the ``scheme://{lookup}``
    shape this package emits.
    """
    parts: list[str] = []
    last: int = 0
    for match in _TEMPLATE_VAR.finditer(uri_template):
        parts.append(re.escape(uri_template[last : match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^/]+)")
        last = match.end()
    parts.append(re.escape(uri_template[last:]))
    return re.compile("^" + "".join(parts) + "$")


class ResourceRegistry:
    """URI or URI-template to
    [`ResourceBinding`][rest_framework_mcp.registry.types.resource_binding.ResourceBinding]
    lookup.

    Concrete resources are matched by exact URI, templates by a regex derived
    from the template. ``resolve`` returns the binding plus the variables
    extracted from the URI.

    **Specificity, not registration order.** A template's ``{var}`` matches any
    single segment, so ``reports://{report_id}`` also matches
    ``reports://all-tenants-summary``. Resolving in registration order would
    make *which permission stack guards a URI* a function of the order the two
    were registered in — and the wrong answer is the permissive one, since the
    template is the general case. Concrete URIs are therefore tried first, and
    only then templates.
    """

    def __init__(self) -> None:
        self._bindings: list[ResourceBinding] = []
        self._patterns: dict[str, re.Pattern[str]] = {}

    def register(self, binding: ResourceBinding) -> None:
        for existing in self._bindings:
            if existing.uri_template == binding.uri_template:
                raise ValueError(f"Duplicate MCP resource URI: {binding.uri_template!r}")
        self._bindings.append(binding)
        self._patterns[binding.uri_template] = _template_to_pattern(binding.uri_template)

    def resolve(self, uri: str) -> tuple[ResourceBinding, dict[str, str]] | None:
        # Concrete before template — see the class docstring. Within each group
        # registration order still decides, which is only reachable for two
        # templates whose patterns overlap.
        for binding in (*self.concrete(), *self.templates()):
            pattern: re.Pattern[str] = self._patterns[binding.uri_template]
            match: re.Match[str] | None = pattern.match(uri)
            if match is not None:
                return binding, match.groupdict()
        return None

    def by_uri_template(self, uri_template: str) -> ResourceBinding | None:
        """Exact lookup on the registered template string.

        A caller holding the template itself — the completion API's
        ``ref/resource``, say — must use this rather than ``resolve``:
        ``things://{pk}`` satisfies its own pattern with ``pk="{pk}"``, so
        ``resolve`` would answer, plausibly and wrongly.
        """
        for binding in self._bindings:
            if binding.uri_template == uri_template:
                return binding
        return None

    def all(self) -> list[ResourceBinding]:
        return list(self._bindings)

    def concrete(self) -> list[ResourceBinding]:
        return [b for b in self._bindings if not b.is_template]

    def templates(self) -> list[ResourceBinding]:
        return [b for b in self._bindings if b.is_template]

    def __len__(self) -> int:
        return len(self._bindings)


__all__ = ["ResourceRegistry"]
