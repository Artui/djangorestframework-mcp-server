from __future__ import annotations

from enum import Enum


class OutputFormat(str, Enum):
    """Output format for the human-readable text block of a ``ToolResult``.

    ``structuredContent`` is always JSON; this enum only controls the
    encoding of the ``content[0]`` text block:

    - ``JSON``: pretty-printed JSON, the safe default.
    - ``TOON``: token-oriented object notation. Compact for large uniform
      arrays; falls back to JSON if the optional ``toon`` extra is not
      installed.
    - ``AUTO``: encoder picks per-payload — TOON for uniform list-of-objects,
      JSON otherwise.
    """

    JSON = "json"
    TOON = "toon"
    AUTO = "auto"

    @classmethod
    def coerce(cls, value: OutputFormat | str | None) -> OutputFormat:
        """Accept either an enum member or its string value; default to JSON."""
        if value is None:
            return cls.JSON
        if isinstance(value, cls):
            return value
        return cls(value)


__all__ = ["OutputFormat"]
