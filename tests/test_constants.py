"""Tests for ``rest_framework_mcp.constants`` — the package's enum + constant home."""

from __future__ import annotations

import pytest

from rest_framework_mcp.constants import (
    JSONRPC_VERSION,
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    ArgumentBinding,
    JsonRpcErrorCode,
    OutputFormat,
    ToolKind,
    UnknownArguments,
)

# ---------- OutputFormat ----------


def test_output_format_coerce_none_returns_json() -> None:
    assert OutputFormat.coerce(None) is OutputFormat.JSON


def test_output_format_coerce_enum_passthrough() -> None:
    assert OutputFormat.coerce(OutputFormat.TOON) is OutputFormat.TOON


def test_output_format_coerce_string() -> None:
    assert OutputFormat.coerce("auto") is OutputFormat.AUTO


def test_output_format_coerce_invalid_string_raises() -> None:
    with pytest.raises(ValueError):
        OutputFormat.coerce("nonsense")


# ---------- ArgumentBinding ----------


def test_argument_binding_has_three_members() -> None:
    members = {m.name for m in ArgumentBinding}
    assert members == {"DATA_ONLY", "MERGE", "REPLACE"}


def test_argument_binding_members_identity() -> None:
    assert ArgumentBinding.DATA_ONLY is ArgumentBinding.DATA_ONLY
    assert ArgumentBinding.MERGE is not ArgumentBinding.DATA_ONLY
    assert ArgumentBinding.REPLACE is not ArgumentBinding.MERGE


def test_argument_binding_str_value_is_internal_only() -> None:
    """The string-shaped value is internal — never expected at call sites.

    ``ArgumentBinding`` is a plain ``Enum`` (not ``str, Enum``), so members
    don't compare equal to their string values. This is intentional —
    forces API consumers to pass the enum member.
    """
    assert ArgumentBinding.MERGE != "merge"
    assert ArgumentBinding.MERGE.value == "merge"


# ---------- JSON-RPC envelope ----------


def test_jsonrpc_version_is_2_0() -> None:
    assert JSONRPC_VERSION == "2.0"


def test_json_rpc_error_code_standard_set() -> None:
    """Spot-check the standard JSON-RPC + MCP-reserved codes are pinned."""
    assert JsonRpcErrorCode.PARSE_ERROR == -32700
    assert JsonRpcErrorCode.INVALID_PARAMS == -32602
    assert JsonRpcErrorCode.INTERNAL_ERROR == -32603
    assert JsonRpcErrorCode.SERVER_ERROR == -32000
    assert JsonRpcErrorCode.RATE_LIMITED == -32005


# ---------- Reserved-key sets ----------


def test_reserved_post_fetch_keys_shape() -> None:
    assert frozenset({"ordering", "page", "limit"}) == RESERVED_POST_FETCH_KEYS


def test_reserved_pool_seeds_shape() -> None:
    assert frozenset({"request", "user", "data"}) == RESERVED_POOL_SEEDS


def test_reserved_sets_are_disjoint() -> None:
    """Post-fetch keys and pool seeds describe distinct concerns; no overlap."""
    assert not (RESERVED_POST_FETCH_KEYS & RESERVED_POOL_SEEDS)


# ---------- ToolKind ----------


def test_tool_kind_has_two_members() -> None:
    assert {m.name for m in ToolKind} == {"SERVICE", "SELECTOR"}


def test_tool_kind_str_value_is_internal_only() -> None:
    """Plain :class:`Enum` — members don't compare equal to their string values."""
    assert ToolKind.SERVICE != "service"
    assert ToolKind.SELECTOR.value == "selector"


# ---------- UnknownArguments ----------


def test_unknown_arguments_has_three_members() -> None:
    assert {m.name for m in UnknownArguments} == {"REJECT", "PASSTHROUGH", "IGNORE"}


def test_unknown_arguments_str_value_is_internal_only() -> None:
    assert UnknownArguments.REJECT != "reject"
    assert UnknownArguments.PASSTHROUGH.value == "passthrough"
