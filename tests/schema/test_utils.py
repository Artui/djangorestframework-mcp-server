"""``end_sentence`` — appended wording never runs on from text it did not write."""

from __future__ import annotations

import pytest

from rest_framework_mcp.schema.utils import end_sentence


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("`items` field is not found", "`items` field is not found."),
        ("This field is required.", "This field is required."),
        ("Really?", "Really?"),
        ("Stop!", "Stop!"),
        ("trailing space  ", "trailing space."),
    ],
)
def test_end_sentence(text: str, expected: str) -> None:
    assert end_sentence(text) == expected
