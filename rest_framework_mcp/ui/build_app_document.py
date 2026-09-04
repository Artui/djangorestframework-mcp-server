from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from django.utils.html import escape

# Deliberately close to nothing. The view is drawn inside the host's own chrome,
# in a light or dark theme this document does not choose, and `prefersBorder`
# already asks the host for a frame -- so a shell that painted a background, a
# card or a padding box would be a second frame inside the first, which is the
# note every consumer eventually writes back to us about.
#
# `color-scheme` is what makes an unstyled view legible in both themes without
# naming a single colour: it hands the UA's own `Canvas` / `CanvasText` pair to
# the document. Host-supplied CSS variables win where they exist, and the view's
# own `<style>` comes later in the document, so it wins over everything here.
_BASE_STYLES = """
:root { color-scheme: light dark; }
html[data-theme="light"] { color-scheme: light; }
html[data-theme="dark"] { color-scheme: dark; }
body {
  margin: 0;
  background: transparent;
  color: var(--text-primary, CanvasText);
  font-family: var(--font-family, system-ui, -apple-system, "Segoe UI", sans-serif);
  font-size: var(--font-size, 13px);
  line-height: 1.5;
}
#mcp-app-error {
  margin: 0 0 8px;
  padding: 8px 10px;
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  border: 1px solid var(--border, currentColor);
  border-radius: 4px;
}
"""


def build_app_document(body: str, *, title: str) -> str:
    """Wrap a view's markup in a complete MCP Apps document.

    ``body`` is a *fragment* -- the view's own markup, styles and scripts, with
    no ``<html>`` around it. What comes back is the whole document: the element
    structure a sandbox expects, a minimal theme-inheriting stylesheet, and the
    ``ui/*`` postMessage bridge inlined ahead of the fragment so the fragment can
    assign ``mcpApp.onToolResult`` while the parser is still running.

    Reached most easily through ``register_ui_resource(body_template_name=...)``,
    which renders a Django template into ``body``. It is public because the other
    content sources deserve the same shell: a project assembling its markup some
    other way can wrap the result and pass it as ``html=`` or return it from a
    ``selector=``.

    Three properties are structural rather than advisory, because each of them
    cost a consumer at least one debugging round:

    - **``<html>``, ``<head>`` and ``<body>`` are written out.** HTML5 infers all
      three and browsers do not care, but the sandbox loads this document as raw
      HTML and applies a CSP to it, and a sandbox injecting anything into
      ``<head>`` has nowhere to put it when the element is implied.
    - **Nothing is fetched.** No CDN, no module import, no external stylesheet --
      which is the extension's own advice, and means a view needs no
      ``resource_domains`` in its CSP just to boot.
    - **The bridge always completes its handshake**, so the frame is always
      revealed and a broken view can say what is wrong. See ``bridge.js``.

    Args:
        body: The view's markup. Inserted verbatim -- it is HTML by definition,
            and it is the project's own template output, not caller input.
        title: The document title. Escaped.

    Returns:
        A complete ``text/html`` document.
    """
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>{_BASE_STYLES}</style>\n"
        f"<script>{_bridge_source()}</script>\n"
        "</head>\n"
        "<body>\n"
        f'<div id="mcp-app-root">{body}</div>\n'
        "</body>\n"
        "</html>\n"
    )


@lru_cache(maxsize=1)
def _bridge_source() -> str:
    """The bridge, read from the packaged file once per process.

    Cached because a view is composed on every ``resources/read``, and the file
    cannot change under a running process the way a Django template can -- it
    ships in the wheel.
    """
    return files("rest_framework_mcp.ui").joinpath("bridge.js").read_text(encoding="utf-8")


__all__ = ["build_app_document"]
