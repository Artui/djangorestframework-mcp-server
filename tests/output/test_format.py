from __future__ import annotations

import pytest

from rest_framework_mcp.output.format import OutputFormat


def test_coerce_none_returns_json() -> None:
    assert OutputFormat.coerce(None) is OutputFormat.JSON


def test_coerce_enum_passthrough() -> None:
    assert OutputFormat.coerce(OutputFormat.TOON) is OutputFormat.TOON


def test_coerce_string() -> None:
    assert OutputFormat.coerce("auto") is OutputFormat.AUTO


def test_coerce_invalid_string_raises() -> None:
    with pytest.raises(ValueError):
        OutputFormat.coerce("nonsense")
