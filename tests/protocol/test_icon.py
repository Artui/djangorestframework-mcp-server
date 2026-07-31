"""``Icon`` — the display-metadata record every wire type can carry."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp import Icon, IconTheme


def test_minimal_icon_emits_only_src() -> None:
    assert Icon(src="https://example.test/i.png").to_dict() == {"src": "https://example.test/i.png"}


def test_full_icon_emits_every_field() -> None:
    icon = Icon(
        src="https://example.test/i.svg",
        mime_type="image/svg+xml",
        sizes=("any",),
        theme=IconTheme.DARK,
    )
    assert icon.to_dict() == {
        "src": "https://example.test/i.svg",
        "mimeType": "image/svg+xml",
        "sizes": ["any"],
        "theme": "dark",
    }


def test_data_uri_is_accepted() -> None:
    src = "data:image/png;base64,iVBORw0KGgo="
    assert Icon(src=src).to_dict()["src"] == src


@pytest.mark.parametrize(
    "src",
    [
        "http://example.test/i.png",
        "file:///tmp/i.png",
        "javascript:alert(1)",
        "/static/i.png",
    ],
)
def test_non_https_non_data_schemes_are_refused(src: str) -> None:
    """Clients are required to reject these, so registration refuses them first.

    A rejected icon is not a degraded icon — it is one the user never sees,
    with nothing in the logs to say why.
    """
    with pytest.raises(ImproperlyConfigured, match="https: or data:"):
        Icon(src=src)


def test_scheme_comparison_is_case_insensitive() -> None:
    assert Icon(src="HTTPS://example.test/i.png").to_dict()["src"].startswith("HTTPS")
