"""Every name a module advertises in ``__all__`` must be a name it actually binds.

``__all__`` is a claim, and nothing else checks it. A name listed but never
imported passes lint, passes types, passes the whole suite — and fails for the
first person who writes ``from rest_framework_mcp.contrib.oauth import <name>``,
which is exactly the spelling the docs teach. It also breaks ``import *``
outright, with an ``AttributeError`` naming a symbol that plainly exists one
file away.

That happened here: ``check_oauth_url_shadowing`` sat in the OAuth package's
``__all__`` with no import beside it.

⭐ **Checked statically, against the source, rather than with ``hasattr`` on the
imported module** — and the difference is the whole test. Importing
``pkg.thing`` binds ``thing`` as an attribute of ``pkg``, so once anything has
imported the submodule (a sibling, a test, the walk itself), ``hasattr(pkg,
"thing")`` is ``True`` while ``from pkg import thing`` hands back a *module*
that raises ``TypeError: 'module' object is not callable``. A runtime check
reports that as fine. The first draft of this test did exactly that and passed
with the defect reintroduced.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import rest_framework_mcp

PACKAGE_ROOT = pathlib.Path(rest_framework_mcp.__file__).parent


def _bound_names(tree: ast.Module) -> set[str]:
    """Every name this module body binds at import time."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _declared_all(tree: ast.Module) -> list[str] | None:
    """The module's ``__all__`` as a literal, or ``None`` when it has none."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            try:
                return list(ast.literal_eval(node.value))
            except ValueError:
                return None
    return None


def _sources() -> list[tuple[str, list[str], set[str]]]:
    found: list[tuple[str, list[str], set[str]]] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        declared = _declared_all(tree)
        if declared is None:
            continue
        if any(
            isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
            for n in ast.walk(tree)
        ):
            # A star import makes the bound set unknowable from the source.
            continue
        found.append((str(path.relative_to(PACKAGE_ROOT)), declared, _bound_names(tree)))
    return found


SOURCES = _sources()


def test_the_scan_found_the_package() -> None:
    """Guards the guard: an empty scan would make every check below vacuous."""
    assert len(SOURCES) > 50
    assert any(name == "contrib/oauth/__init__.py" for name, _, _ in SOURCES)


@pytest.mark.parametrize(
    ("relative_path", "declared", "bound"),
    SOURCES,
    ids=[name for name, _, _ in SOURCES],
)
def test_all_names_are_bound(relative_path: str, declared: list[str], bound: set[str]) -> None:
    missing = sorted(set(declared) - bound)
    assert not missing, (
        f"{relative_path} declares {missing} in __all__ but never binds "
        "them — 'from … import' fails for those names, and 'import *' fails outright"
    )
