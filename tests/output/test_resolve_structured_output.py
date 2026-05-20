from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output


def _resolve(
    *,
    schema: bool | None,
    content: bool | None,
    name: str = "t",
) -> tuple[bool, bool]:
    return resolve_structured_output(
        include_output_schema_override=schema,
        include_structured_content_override=content,
        binding_name=name,
    )


# ---------- structured_content resolution ----------


def test_structured_content_explicit_true_wins_over_global_off(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_STRUCTURED_CONTENT": False}
    _, content = _resolve(schema=False, content=True)
    assert content is True


def test_structured_content_explicit_false_wins_over_global_on(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_STRUCTURED_CONTENT": True}
    _, content = _resolve(schema=False, content=False)
    assert content is False


def test_structured_content_none_inherits_global_true(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_STRUCTURED_CONTENT": True}
    _, content = _resolve(schema=False, content=None)
    assert content is True


def test_structured_content_none_inherits_global_false(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "INCLUDE_STRUCTURED_CONTENT": False,
        "INCLUDE_OUTPUT_SCHEMA": False,
    }
    _, content = _resolve(schema=None, content=None)
    assert content is False


# ---------- output_schema resolution ----------


def test_output_schema_explicit_false_wins_over_global_on(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_OUTPUT_SCHEMA": True}
    schema, _ = _resolve(schema=False, content=True)
    assert schema is False


def test_output_schema_none_inherits_global_true(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_OUTPUT_SCHEMA": True}
    schema, _ = _resolve(schema=None, content=True)
    assert schema is True


def test_output_schema_none_inherits_global_false(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_OUTPUT_SCHEMA": False}
    schema, _ = _resolve(schema=None, content=True)
    assert schema is False


# ---------- defaults ----------


def test_defaults_are_true_when_settings_absent(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    schema, content = _resolve(schema=None, content=None)
    assert schema is True
    assert content is True


# ---------- spec invariant ----------


def test_raises_when_schema_advertised_but_content_suppressed(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "INCLUDE_OUTPUT_SCHEMA": True,
        "INCLUDE_STRUCTURED_CONTENT": False,
    }
    with pytest.raises(ImproperlyConfigured) as excinfo:
        _resolve(schema=None, content=None, name="weather")
    msg = str(excinfo.value)
    assert "weather" in msg
    assert "outputSchema" in msg
    assert "structuredContent" in msg


def test_raises_when_per_binding_schema_forced_on_with_content_off() -> None:
    """Per-binding overrides clash with each other even when globals agree."""
    with pytest.raises(ImproperlyConfigured):
        _resolve(schema=True, content=False)


def test_allows_structured_content_without_schema(settings) -> None:
    """SEP-1624: structuredContent without outputSchema is spec-allowed."""
    settings.REST_FRAMEWORK_MCP = {
        "INCLUDE_OUTPUT_SCHEMA": False,
        "INCLUDE_STRUCTURED_CONTENT": True,
    }
    schema, content = _resolve(schema=None, content=None)
    assert schema is False
    assert content is True


def test_allows_both_disabled(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "INCLUDE_OUTPUT_SCHEMA": False,
        "INCLUDE_STRUCTURED_CONTENT": False,
    }
    schema, content = _resolve(schema=None, content=None)
    assert schema is False
    assert content is False
