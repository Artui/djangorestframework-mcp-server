"""Check documentation snippets against the code they claim to use.

The existing gates verify our *own* surface: ruff, ``ty`` and
``mkdocs --strict`` all stop at this package's boundary. Nothing checks a claim
about a **dependency's** API — and that is where doc rot is both most likely and
least visible, because the dependency moves on its own schedule and no test ever
imports the snippet.

This closes that gap statically. For every ``python`` fence in ``docs/`` and
``README.md`` it:

1. parses the snippet — a syntax error is a failure, since nobody can run it;
2. resolves every ``from X import Y`` against the **installed** packages,
   whoever owns them, and fails on a symbol that no longer exists;
3. binds the arguments of every call whose callee it can resolve against the
   real signature — which catches an unknown keyword *and* an argument passed
   positionally to a keyword-only parameter, the second of which reads
   perfectly and raises ``TypeError`` for the first person to run it.

What it deliberately does **not** do: execute the snippets (most reference a
reader's own modules), or check semantics. It answers "does this still exist,
spelled this way" — the class of error that accumulates silently.

It also cannot see non-Python snippets. A JavaScript example against a frontend
package stays unchecked, and stays a matter for review.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import os
import pathlib
import re
import sys
import textwrap
from typing import Any

import django

# The info string after ``python`` is optional: mkdocs-material titles a fence
# with ``python title="urls.py"``, and those are the copy-paste-this-into-your-
# project examples. Matching only a bare ```python skipped every one of them
# while still reporting a clean run — the failure this whole script exists to
# prevent, in the script itself.
FENCE = re.compile(r"```python[^\n]*\n(.*?)```", re.S)

# Stands in for a snippet argument whose value is irrelevant: only whether it
# can be passed at all is being checked.
_PLACEHOLDER = object()


def _iter_fences(root: pathlib.Path) -> list[tuple[pathlib.Path, int, str]]:
    sources = sorted(root.joinpath("docs").rglob("*.md"))
    readme = root / "README.md"
    if readme.exists():
        sources.append(readme)
    fences: list[tuple[pathlib.Path, int, str]] = []
    for path in sources:
        for index, match in enumerate(FENCE.finditer(path.read_text()), start=1):
            fences.append((path, index, textwrap.dedent(match.group(1))))
    return fences


def _is_readers_own_code(module: str) -> bool:
    """Whether ``module`` is a stand-in for the reader's project.

    Decided by whether its **root package** is installed — not by a name list,
    which would go stale, and not by "the import failed", which would swallow
    the most important finding of all.

    That distinction is the point. ``myproject.agent`` has no installed root,
    so it is the reader's own code and unresolvable by design. But
    ``some_dep.contrib.store`` whose root *is* installed and whose submodule is
    **gone** is a dependency that moved out from under the docs — which has
    happened here before, and is exactly what this must fail on.
    """
    root = module.split(".")[0]
    try:
        return importlib.util.find_spec(root) is None
    except (ImportError, ValueError):
        return False


def _resolve_imports(tree: ast.AST) -> tuple[dict[str, Any], list[str]]:
    """Map each imported name to the live object, collecting failures."""
    resolved: dict[str, Any] = {}
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.level or _is_readers_own_code(node.module):
            continue
        try:
            module = importlib.import_module(node.module)
        except Exception as exc:  # noqa: BLE001 - report it, don't abort the run
            problems.append(f"cannot import {node.module!r}: {exc}")
            continue
        for alias in node.names:
            if not hasattr(module, alias.name):
                problems.append(f"{node.module!r} has no {alias.name!r}")
                continue
            resolved[alias.asname or alias.name] = getattr(module, alias.name)
    return resolved, problems


def _check_calls(tree: ast.AST, resolved: dict[str, Any]) -> list[str]:
    """Check every resolvable call against the real signature.

    Binding the arguments rather than only checking keyword *names* is what
    catches the other half of this class: a snippet passing an argument
    positionally to a parameter the callee declares keyword-only. That reads
    perfectly and raises ``TypeError`` for the first person to run it, and a
    name-only check cannot see it — the keyword was never written down.
    """
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        target = resolved.get(node.func.id)
        if target is None or not (inspect.isclass(target) or inspect.isfunction(target)):
            continue
        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError):
            continue
        problems.extend(_bind_problems(node, signature))
    return problems


def _bind_problems(node: ast.Call, signature: inspect.Signature) -> list[str]:
    """Bind a snippet's arguments to ``signature``, reporting what will not fit.

    ``bind_partial`` rather than ``bind`` because a snippet legitimately omits
    required arguments it has already shown elsewhere; what it must not do is
    pass one the callee cannot accept. Starred arguments make the real arity
    unknowable statically, so a call carrying one is skipped rather than
    guessed at.
    """
    name = node.func.id if isinstance(node.func, ast.Name) else "call"
    if any(isinstance(a, ast.Starred) for a in node.args) or any(
        k.arg is None for k in node.keywords
    ):
        return []
    try:
        signature.bind_partial(
            *[_PLACEHOLDER] * len(node.args),
            **{k.arg: _PLACEHOLDER for k in node.keywords if k.arg is not None},
        )
    except TypeError as exc:
        return [f"{name}(...) does not match its signature {signature}: {exc}"]
    return []


def main() -> int:
    # Docs import Django-dependent modules; without a configured settings module
    # every one of those reads as a failure and the real signal is lost.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.conftest_settings")
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    django.setup()

    root = pathlib.Path(__file__).resolve().parent.parent
    failures: list[str] = []
    checked = 0
    for path, index, source in _iter_fences(root):
        where = f"{path.relative_to(root)} fence #{index}"
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            failures.append(f"{where}: does not parse — {exc.msg} (line {exc.lineno})")
            continue
        checked += 1
        resolved, problems = _resolve_imports(tree)
        problems += _check_calls(tree, resolved)
        failures.extend(f"{where}: {problem}" for problem in problems)

    if failures:
        print(f"Documentation drift ({len(failures)}):", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(f"Checked {checked} Python snippets against the installed packages. Clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
