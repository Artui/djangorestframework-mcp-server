from __future__ import annotations

from django.test import override_settings

from rest_framework_mcp.protocol.build_server_info import build_server_info
from rest_framework_mcp.version import __version__ as package_version


def test_explicit_name_and_version_win_over_settings() -> None:
    with override_settings(REST_FRAMEWORK_MCP={"SERVER_INFO": {"name": "s", "version": "9.9"}}):
        info = build_server_info(name="internal", version="1.2.3")
    assert info.name == "internal"
    assert info.version == "1.2.3"


def test_omitted_fields_fall_back_to_server_info_setting() -> None:
    """``SERVER_INFO`` stays the default source, so a project that configures it
    and never passes ``name=`` keeps its wire identity."""
    with override_settings(
        REST_FRAMEWORK_MCP={"SERVER_INFO": {"name": "configured", "version": "0.0.1"}}
    ):
        info = build_server_info()
    assert info.name == "configured"
    assert info.version == "0.0.1"


def test_each_field_falls_back_independently() -> None:
    with override_settings(REST_FRAMEWORK_MCP={"SERVER_INFO": {"name": "s", "version": "9.9"}}):
        info = build_server_info(name="internal")
    assert info.name == "internal"
    assert info.version == "9.9"


def test_empty_server_info_falls_back_to_package_defaults() -> None:
    with override_settings(REST_FRAMEWORK_MCP={"SERVER_INFO": {}}):
        info = build_server_info()
    assert info.name == "djangorestframework-mcp-server"
    assert info.version == package_version
