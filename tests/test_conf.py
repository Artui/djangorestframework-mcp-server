from __future__ import annotations

import pytest

from rest_framework_mcp.conf import get_setting


def test_get_setting_unknown_key_raises() -> None:
    with pytest.raises(KeyError, match="Unknown"):
        get_setting("NOT_A_REAL_SETTING")


def test_get_setting_falls_back_to_default(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {}
    assert get_setting("MAX_REQUEST_BYTES") == 1_048_576


def test_get_setting_user_override(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"MAX_REQUEST_BYTES": 100}
    assert get_setting("MAX_REQUEST_BYTES") == 100


def test_get_setting_handles_missing_attribute(settings) -> None:
    if hasattr(settings, "REST_FRAMEWORK_MCP"):
        del settings.REST_FRAMEWORK_MCP
    # No REST_FRAMEWORK_MCP defined → defaults are returned.
    assert get_setting("MAX_REQUEST_BYTES") == 1_048_576
