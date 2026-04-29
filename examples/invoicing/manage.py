#!/usr/bin/env python
"""Django ``manage.py`` for the invoicing MCP example."""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "invoicing.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Couldn't import Django. Activate the virtualenv and `uv pip install -e '../..'`."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
