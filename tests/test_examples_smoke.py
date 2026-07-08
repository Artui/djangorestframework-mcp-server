"""Smoke test: the tracked examples import + build against the shipped API.

The examples are standalone Django projects (their own settings + apps), so each
is exercised in a subprocess with its own ``DJANGO_SETTINGS_MODULE`` rather than
imported into this suite's Django context. ``build_server()`` performs every
tool / resource / prompt registration, so a stale API surfaces here as a
non-zero exit — the guard that would have caught the 0.8 ``filter_set=`` /
missing-``kind`` breakage.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_EXAMPLES = [
    pytest.param("invoicing", "invoicing.settings", "invoices.mcp", id="invoicing"),
    pytest.param("job_status", "job_status.settings", "jobs.mcp", id="job_status"),
]


@pytest.mark.parametrize(("project", "settings_module", "mcp_module"), _EXAMPLES)
def test_example_build_server(project: str, settings_module: str, mcp_module: str) -> None:
    root = _EXAMPLES_DIR / project
    code = f"import django; django.setup()\nimport {mcp_module} as m\nm.build_server()"
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": settings_module}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
