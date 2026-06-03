"""Unit coverage for ``ChainContext`` access helpers."""

from __future__ import annotations

from rest_framework_mcp.registry.types.chain_context import ChainContext


def test_getitem_and_contains() -> None:
    ctx = ChainContext(args={"x": 1}, request=None, user=None)
    ctx.outputs["acct"] = "ACCT"
    assert ctx["acct"] == "ACCT"
    assert "acct" in ctx
    assert "missing" not in ctx
    assert ctx.args == {"x": 1}
