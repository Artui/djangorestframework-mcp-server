"""The repo's own ``scripts/`` stay importable against the package they drive.

Nothing in the standard gates covers them: they are not referenced from the
Makefile's test target, and ``ty`` is scoped to ``rest_framework_mcp/`` only. So
a package-wide rename — the move of types into ``types/`` sub-packages, say —
leaves a script pointing at a module path that no longer exists, and the first
person to find out is whoever follows the script's own documented invocation.

Imports are checked rather than run: a benchmark takes minutes and its numbers
are not an assertion. What rots is the import surface.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _package_imports(source: str) -> list[tuple[str, str]]:
    """``(module, symbol)`` for every ``from rest_framework_mcp… import …``."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "rest_framework_mcp"
        ):
            found.extend((node.module or "", alias.name) for alias in node.names)
    return found


@pytest.mark.parametrize(
    "script", sorted(p.name for p in _SCRIPTS.glob("*.py")), ids=lambda name: name
)
def test_a_script_imports_only_module_paths_that_exist(script: str) -> None:
    # A script that imports nothing from the package (a docs checker driving
    # only mkdocs, say) has no import surface to rot, and is not a failure.
    for module, symbol in _package_imports((_SCRIPTS / script).read_text()):
        resolved = importlib.import_module(module)
        assert hasattr(resolved, symbol), f"{module} has no {symbol!r} ({script})"
