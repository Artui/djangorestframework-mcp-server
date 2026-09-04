"""``build_app_document`` — the shell the package wraps a view fragment in.

The assertions here are about *document shape*, which is the half a consumer
cannot check from inside a host: a view whose shell is wrong does not fail, it
renders nothing, in a frame nobody can see. The bridge's own behaviour is
driven for real in ``test_bridge.py``.
"""

from __future__ import annotations

import re

import pytest

from rest_framework_mcp.ui.build_app_document import build_app_document

BODY = '<div id="rows">Waiting.</div>'


@pytest.fixture
def document() -> str:
    return build_app_document(BODY, title="Invoices")


class TestDocumentShape:
    def test_the_three_implied_elements_are_written_out(self, document: str) -> None:
        """HTML5 infers ``html``/``head``/``body`` and browsers do not care, but
        the sandbox loads this as raw HTML and applies a CSP to it — and a
        sandbox injecting anything into ``<head>`` has nowhere to put it when
        the element is implied. Every reference view writes them out."""
        assert document.startswith('<!doctype html>\n<html lang="en">')
        assert "<head>" in document
        assert "<body>" in document
        assert document.rstrip().endswith("</html>")

    def test_the_fragment_is_inserted_verbatim_inside_the_content_element(
        self, document: str
    ) -> None:
        """``#mcp-app-root`` is not decoration: the bridge measures *it* rather
        than ``documentElement`` when it reports size."""
        assert f'<div id="mcp-app-root">{BODY}</div>' in document

    def test_the_title_is_escaped(self) -> None:
        document = build_app_document(BODY, title='Invoices & "co" <b>')
        assert "<title>Invoices &amp; &quot;co&quot; &lt;b&gt;</title>" in document

    def test_the_bridge_is_inlined_ahead_of_the_body(self, document: str) -> None:
        """A fragment assigns ``mcpApp.onToolResult`` while the parser is still
        running, so ``mcpApp`` has to exist before the fragment is reached."""
        assert "window.mcpApp = mcpApp;" in document
        assert document.index("window.mcpApp") < document.index("<body>")


class TestNothingIsFetched:
    """The property that keeps a view bootable with an empty CSP.

    The recipe this replaces imported the extension's SDK from a CDN, which
    meant every view needed ``resource_domains`` declared just to start, and
    contradicted the extension's own advice to inline everything. A single
    ``<script src=...>`` added here later would put that back silently — the
    view would still work wherever the author tested it, and fail on any host
    whose CSP is stricter than theirs.
    """

    def test_no_external_subresource(self, document: str) -> None:
        assert not re.search(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", document)

    def test_no_module_import_or_css_import(self, document: str) -> None:
        assert "@import" not in document
        assert not re.search(r"""\bfrom\s+["']https?://""", document)


class TestTheShellStaysOutOfTheWay:
    def test_it_paints_no_background(self, document: str) -> None:
        """``prefersBorder`` asks the host for a frame, and the host draws its
        own chrome regardless. A shell with a background would be a third one."""
        assert "background: transparent;" in document

    def test_it_names_no_colour_of_its_own(self, document: str) -> None:
        """The same view is drawn in light and dark hosts. ``color-scheme``
        plus the host's CSS variables is what makes that work without the
        document choosing a palette."""
        assert "color-scheme: light dark;" in document
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", document.split("</style>")[0])
