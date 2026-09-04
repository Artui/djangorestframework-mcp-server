"""The document half of MCP Apps: the shell and the ``ui/*`` bridge a view needs.

Everything else in this package *declares* a view and serves its bytes. This is
the one place that writes any of the view itself, and it exists for a single
reason: the bridge's failure modes are all silent, and one of them is
unrecoverable. See ``bridge.js``.
"""

from rest_framework_mcp.ui.build_app_document import build_app_document

__all__ = ["build_app_document"]
