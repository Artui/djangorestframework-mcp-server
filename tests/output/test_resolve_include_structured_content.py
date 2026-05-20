from __future__ import annotations

from rest_framework_mcp.output.resolve_include_structured_content import (
    resolve_include_structured_content,
)


def test_explicit_true_wins_over_global_off(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_STRUCTURED_CONTENT": False}
    assert resolve_include_structured_content(True) is True


def test_explicit_false_wins_over_global_on(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_STRUCTURED_CONTENT": True}
    assert resolve_include_structured_content(False) is False


def test_none_inherits_global_true(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_STRUCTURED_CONTENT": True}
    assert resolve_include_structured_content(None) is True


def test_none_inherits_global_false(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"INCLUDE_STRUCTURED_CONTENT": False}
    assert resolve_include_structured_content(None) is False


def test_default_is_true_when_setting_absent(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    assert resolve_include_structured_content(None) is True
