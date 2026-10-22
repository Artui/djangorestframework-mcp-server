"""Tests for ``rest_framework_mcp.constants`` — the package's enum + constant home."""

from __future__ import annotations

import pytest
from rest_framework_services.dispatch.utils import DROPPED_EDGE_SEEDS, EDGE_SEEDS

from rest_framework_mcp.constants import (
    JSONRPC_VERSION,
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    RESOURCE_EDGE_SEEDS,
    TOOL_EDGE_SEEDS,
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


def test_argument_binding_re_exports_drf_services_members() -> None:
    """``ArgumentBinding`` is re-exported from drf-services (the dispatch core)."""
    members = {m.name for m in ArgumentBinding}
    assert members == {"AUTO", "BUNDLE", "SPREAD_AUTHOR_WINS", "SPREAD_CALLER_WINS"}


def test_argument_binding_members_identity() -> None:
    assert ArgumentBinding.BUNDLE is ArgumentBinding.BUNDLE
    assert ArgumentBinding.SPREAD_AUTHOR_WINS is not ArgumentBinding.BUNDLE
    assert ArgumentBinding.SPREAD_CALLER_WINS is not ArgumentBinding.SPREAD_AUTHOR_WINS


def test_argument_binding_str_value_is_internal_only() -> None:
    """The string-shaped value is internal — never expected at call sites.

    ``ArgumentBinding`` is a plain ``Enum`` (not ``str, Enum``), so members
    don't compare equal to their string values. This is intentional —
    forces API consumers to pass the enum member.
    """
    assert ArgumentBinding.SPREAD_AUTHOR_WINS != "spread_author_wins"
    assert ArgumentBinding.SPREAD_AUTHOR_WINS.value == "spread_author_wins"


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
    # ``ordering`` left when ``ordering_fields`` did: the pipeline stopped
    # consuming it, and every name in here is a name a selector cannot use for a
    # parameter of its own -- so an entry the pipeline no longer reads is pure
    # cost, and was silently dropping a value reflection had advertised.
    assert frozenset({"page", "limit"}) == RESERVED_POST_FETCH_KEYS


def test_reserved_pool_seeds_shape() -> None:
    # Re-exported from django-service-specs, which owns the set. The local copy
    # this once replaced had fallen a key behind it (``collection``) — pinning the
    # shape here is what would have caught that, so keep asserting the full set
    # rather than deferring to the import. drf-services 0.58.0 ("Breaking: pool
    # seeds") swapped ``request`` and ``serializer`` for ``result`` and
    # ``queryset``: the DRF edge seeds ``request`` itself, which is why
    # ``RESOURCE_EDGE_SEEDS`` exists. The kernel's 0.14.0 reserves
    # ``call_context``, the one dict a call hands to every stage naming it.
    assert (
        frozenset(
            {
                "user",
                "data",
                "instance",
                "collection",
                "result",
                "queryset",
                "progress",
                "call_context",
            }
        )
        == RESERVED_POOL_SEEDS
    )


def test_the_resource_edge_seeds_are_what_the_kernel_leaves_out() -> None:
    assert frozenset({"request"}) == RESOURCE_EDGE_SEEDS
    assert not (RESOURCE_EDGE_SEEDS & RESERVED_POOL_SEEDS)


def test_the_tool_edge_seeds_are_what_the_drf_edge_fills() -> None:
    # Written out in ``constants.py``; pinned to drf-services' own sets so an
    # upstream change to what the edge seeds or drops fails here.
    assert frozenset(EDGE_SEEDS) | DROPPED_EDGE_SEEDS == TOOL_EDGE_SEEDS
    assert not (TOOL_EDGE_SEEDS & RESERVED_POOL_SEEDS)


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


def test_unknown_arguments_is_a_str_enum() -> None:
    # The kernel's own enum, a ``str`` enum since drf-services 0.58.0 ("``UnknownArguments``
    # is a ``str`` enum ordered ``REJECT``, ``IGNORE``, ``PASSTHROUGH``"), where the
    # earlier plain ``Enum`` compared unequal to its value.
    assert isinstance(UnknownArguments.REJECT, str)
    assert UnknownArguments.REJECT == "reject"
    assert UnknownArguments.PASSTHROUGH.value == "passthrough"
    assert [m.name for m in UnknownArguments] == ["REJECT", "IGNORE", "PASSTHROUGH"]
