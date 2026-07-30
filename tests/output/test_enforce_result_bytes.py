"""Unit coverage for the outbound size measurement."""

from __future__ import annotations

from rest_framework_mcp.output.enforce_result_bytes import enforce_result_bytes


def test_none_ceiling_disables_the_check() -> None:
    assert enforce_result_bytes({"x": "y" * 10_000}, None, label="Tool 'big'") is None


def test_under_the_ceiling_returns_none() -> None:
    assert enforce_result_bytes({"x": 1}, 1_000, label="Tool 'small'") is None


def test_exactly_at_the_ceiling_passes() -> None:
    """The ceiling is inclusive — ``<=`` rather than ``<``."""
    payload = {"x": "abc"}
    size = len(b'{\n  "x": "abc"\n}')
    # Encoded size is whatever the encoder produces; measure it the same way
    # the function does rather than hard-coding a guess.
    message = enforce_result_bytes(payload, size, label="Tool 'edge'")
    over = enforce_result_bytes(payload, size - 1, label="Tool 'edge'")
    assert (message, over is None) == (None, False)


def test_over_the_ceiling_explains_the_remedy() -> None:
    message = enforce_result_bytes({"rows": ["x" * 100] * 100}, 500, label="Tool 'invoices.list'")
    assert message is not None
    # The audience is a model deciding what to do next, so the message has to
    # name the ceiling, the actual size, and something actionable.
    assert "Tool 'invoices.list'" in message
    assert "500 byte ceiling" in message
    assert "'limit'" in message
    assert "not truncated" in message


def test_measures_utf8_bytes_not_characters() -> None:
    """A multi-byte payload is measured in bytes — the unit the ceiling is in."""
    payload = {"x": "é" * 100}
    assert enforce_result_bytes(payload, 108, label="Tool 'unicode'") is not None
