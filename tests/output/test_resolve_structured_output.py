from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output


def _resolve(
    *,
    schema: bool | None,
    content: bool | None,
    name: str = "t",
    default_schema: bool = True,
    default_content: bool = True,
) -> tuple[bool, bool]:
    """Resolve the tri-state overrides against the server-level defaults.

    The defaults arrive as plain booleans (from the owning server's
    ``MCPConfig``) rather than being read from settings here, so these are
    unit tests of the collapse rule with no Django settings in the loop.
    """
    return resolve_structured_output(
        include_output_schema_override=schema,
        include_structured_content_override=content,
        binding_name=name,
        default_output_schema=default_schema,
        default_structured_content=default_content,
    )


# ---------- structured_content resolution ----------


def test_structured_content_explicit_true_wins_over_default_off() -> None:
    _, content = _resolve(schema=False, content=True, default_content=False)
    assert content is True


def test_structured_content_explicit_false_wins_over_default_on() -> None:
    _, content = _resolve(schema=False, content=False, default_content=True)
    assert content is False


def test_structured_content_none_inherits_default_true() -> None:
    _, content = _resolve(schema=False, content=None, default_content=True)
    assert content is True


def test_structured_content_none_inherits_default_false() -> None:
    _, content = _resolve(schema=None, content=None, default_schema=False, default_content=False)
    assert content is False


# ---------- output_schema resolution ----------


def test_output_schema_explicit_false_wins_over_default_on() -> None:
    schema, _ = _resolve(schema=False, content=True, default_schema=True)
    assert schema is False


def test_output_schema_none_inherits_default_true() -> None:
    schema, _ = _resolve(schema=None, content=True, default_schema=True)
    assert schema is True


def test_output_schema_none_inherits_default_false() -> None:
    schema, _ = _resolve(schema=None, content=True, default_schema=False)
    assert schema is False


# ---------- defaults ----------


def test_package_defaults_are_both_on() -> None:
    schema, content = _resolve(schema=None, content=None)
    assert schema is True
    assert content is True


# ---------- spec invariant ----------


def test_raises_when_schema_advertised_but_content_suppressed() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        _resolve(
            schema=None,
            content=None,
            name="weather",
            default_schema=True,
            default_content=False,
        )
    msg = str(excinfo.value)
    assert "weather" in msg
    assert "outputSchema" in msg
    assert "structuredContent" in msg


def test_raises_when_per_binding_schema_forced_on_with_content_off() -> None:
    """Per-binding overrides clash with each other even when the defaults agree."""
    with pytest.raises(ImproperlyConfigured):
        _resolve(schema=True, content=False)


def test_allows_structured_content_without_schema() -> None:
    """SEP-1624: structuredContent without outputSchema is spec-allowed."""
    schema, content = _resolve(
        schema=None, content=None, default_schema=False, default_content=True
    )
    assert schema is False
    assert content is True


def test_allows_both_disabled() -> None:
    schema, content = _resolve(
        schema=None, content=None, default_schema=False, default_content=False
    )
    assert schema is False
    assert content is False
