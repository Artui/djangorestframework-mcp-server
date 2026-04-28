# Repo conventions for `djangorestframework-mcp-server`

This file is the single source of truth for how to write code in this package.
Rules are non-negotiable unless flagged as a heuristic.

## Structural rules

1. **One exported class or function per file.** File name = `snake_case` of the symbol.
   `MCPServer` → `mcp_server.py`; `resolve_callable_kwargs` → `resolve_callable_kwargs.py`,
   or live in a sibling `utils.py` if used by several files in the same package.
2. **Private helpers used in only one file** stay there with a leading `_`.
3. **Non-exported helpers shared across files** go into a sibling `utils.py`. Classes
   are allowed in `utils.py` if they are internal infrastructure.
4. **Top-level imports only.** No function-local / lazy imports unless a circular import
   is genuine and documented inline at the import site, or the dependency is optional
   (e.g. `python-toon`, `oauth2_provider`) — those imports go inside the function body
   with a clear `ImportError` message.
5. **Full type annotations on every function and method signature.** `Any` is allowed
   only at DRF / Django ORM boundaries (e.g. `request.user`, `queryset` items, raw
   request data) where the type genuinely is `Any`.
6. **`__init__.py` is the only re-export point.** Each `__init__.py` lists the public
   surface in `__all__`. Internal modules import from leaf paths, never from the
   package's `__init__`. The top-level `__init__.py` re-exports the user-facing API.
7. **Always `from __future__ import annotations`** at the top of any file with type
   annotations. We support Python 3.10+, so no `match` statements and no PEP 695
   `type` statements.
8. **Absolute imports only.** Imports are ordered stdlib → third-party → first-party
   (`rest_framework_mcp`). Within each block, alphabetical.
9. **No relative imports** (`from .foo import bar` is forbidden).

## No view-layer coupling

The MCP server **does not** wrap, walk, or otherwise reach into DRF viewsets, routers,
or views. Consumers register tools and resources directly using
`rest_framework_services.types.service_spec.ServiceSpec` (for tools / mutations) and
bare selector callables (for resources). The unit of reuse is the `ServiceSpec`, not a
view — a project that uses neither DRF views nor `ServiceViewSet` must still be able to
expose its services over MCP.

This is non-negotiable. Do not import from `rest_framework_services.viewsets`,
`rest_framework_services.views.mutation`, or call `_execute_mutation`. The MCP package
owns its own discovery and dispatch flow.

## No module-level or class-level mutable state

State lives on instances. Module-level constants (lookup tables, regexes, frozen
settings defaults, dispatch tables) are fine — module-level **mutable** state is not.
Specifically:

- No module-level mutable singletons (`_some_warned_flag`, registries, caches).
- No class-level mutable attributes declared on the class body (`_sessions: set = set()`
  is a bug — every instance shares it). Initialise mutables in `__init__`.
- No "warn-once" state stored at module scope. If a warning genuinely belongs
  once-per-process, accept the cost of always-emit and let the consumer filter via
  `warnings.filterwarnings`, or thread the flag through an instance.

This rule exists because module/class-level mutables make tests interfere with each
other and make the package unsafe in multi-tenant or worker-pool deployments. Putting
state on an instance keeps lifetime explicit and ownership obvious.

A corollary: views never look up collaborators (auth backend, session store, registries)
through module-level loaders. They receive them via `as_view(...)` from `MCPServer`.

## Reuse, don't reimplement

The MCP layer is an alternate transport over `djangorestframework-services` primitives.
Import these — do not parallel them:

- `rest_framework_services.types.service_spec.ServiceSpec` — the unit of registration.
- `rest_framework_services.views.utils.resolve_callable_kwargs` — kwarg-pool dispatch.
  Picks the subset of a kwarg pool matching the callable's declared parameters; if the
  callable declares `**kwargs` the entire pool is passed.
- `rest_framework_services.selectors.utils.run_selector` / `arun_selector` — selector
  dispatch with sync/async transparency.
- `rest_framework_services._compat.run_service.run_service` / `arun_service` — service
  dispatch with optional `transaction.atomic()`.
- `rest_framework_services.exceptions.service_error.ServiceError` and
  `service_validation_error.ServiceValidationError` — caught at the MCP boundary and
  mapped to JSON-RPC error responses (`-32602` for `ServiceValidationError`, `-32000`
  for `ServiceError`).

Validation, output-serializer rendering, and kwarg-pool construction are reproduced
locally in the `handlers/` layer — small, transport-shaped equivalents without
view-layer dependencies.

## Tests

- `make test` runs pytest with `--cov=rest_framework_mcp --cov-fail-under=100` (line +
  branch). Restructure rather than reach for `# pragma: no cover`.
- Test layout mirrors the source tree under `tests/`. `rest_framework_mcp/foo/bar.py`
  → `tests/foo/test_bar.py`.
- `tests/testapp/` houses the minimal Django app + sample `MCPServer` factory used by
  the end-to-end suite. Tests must build a fresh server per scenario when they need
  custom configuration — never rely on hidden test-suite globals.
- Async tests: `async def test_...` with pytest-asyncio (`asyncio_mode = "auto"`).
- DB tests: `@pytest.mark.django_db` for sync; `@pytest.mark.django_db(transaction=True)`
  for async.

## Lint and types

- `make lint` runs `ruff check .` + `ty check rest_framework_mcp`. CI fails on either.
- `ruff format` is the source of truth for layout. `make format` writes, `make
  format-check` verifies.
- Pre-commit runs `make lint-fix`, `make format`, `make type-check`. Commits must be
  clean before push — never `--no-verify`.
- `ty` is scoped to `rest_framework_mcp/` only (not tests). For attributes provided by
  Django/DRF MRO that ty can't resolve, declare them as `attr: Any` on the class with a
  comment naming the parent. For unresolvable `super()` calls into DRF mixins, use
  `# ty: ignore[unresolved-attribute]` at the call site.

## Boundaries

- The package **does not** depend on `oauth2_provider` at import time. The
  `DjangoOAuthToolkitBackend` imports it lazily inside its method bodies, gated behind a
  clear `ImportError` message.
- The TOON encoder imports `python-toon` lazily and falls back to JSON with a warning
  if the extra is missing — a tool call must never fail because the optional extra is
  absent.
- The `transport/` layer must not import from `server/` (one-way dependency: server
  composes transport, never the other way).
- `rest_framework_mcp.exceptions` would be the framework-agnostic boundary if introduced
  later — do not import from `rest_framework` inside it.

## Releases

The release pipeline is triggered by pushing a `vX.Y.Z` tag and runs three
sequential jobs in `.github/workflows/release.yml`:

1. `build` — re-runs the full test suite at 100% coverage, then `uv build`.
   It also asserts the git tag matches `rest_framework_mcp.__version__` so
   you can never tag without bumping the source.
2. `publish-pypi` — uploads via **OIDC trusted publishing**. There is no API
   token in the repo; PyPI must have a Trusted Publisher configured for the
   project pointing at this workflow.
3. `publish-docs` — `mkdocs gh-deploy --force --clean` to the `gh-pages`
   branch.

### One-time setup (manual, by the repo owner)

These steps need to happen once before the first tag push will succeed:

1. **PyPI Trusted Publisher** — sign in to PyPI, open the project settings,
   add a "Pending" publisher pointing at `Artui/djangorestframework-mcp-server`,
   workflow `release.yml`, environment `pypi`. Promote it to a real publisher
   after the project exists on PyPI.
2. **GitHub Environment** — create a `pypi` environment under
   `Settings → Environments` (no secrets needed; OIDC handles auth).
3. **GitHub Pages** — under `Settings → Pages`, set "Build and deployment"
   source to "Deploy from a branch", branch `gh-pages`, folder `/`. The first
   tag push creates that branch.

To cut a release: bump `__version__` in `rest_framework_mcp/version.py`,
update `CHANGELOG.md`, commit, then `git tag vX.Y.Z && git push --tags`.

## Common pitfalls

- `async def` Django views require ASGI; the JSON-only POST path must keep working
  under WSGI. Don't reach for `async` unless SSE actually requires it.
- `Origin` validation is mandatory per the MCP spec — do not silently allow
  missing/empty origin against an empty allowlist.
- `MCP-Session-Id` and `MCP-Protocol-Version` are required headers post-`initialize`;
  missing → 400/404 as per spec, not silent acceptance.
- `structuredContent` is always JSON; only the `content[0]` text payload varies by
  `OutputFormat`. TOON output is wrapped in a fenced code block with a leading
  `# format: toon` marker.
- Functions stored as plain class attributes (`service = my_fn`) are returned as bound
  methods when accessed via `self`. Use
  `rest_framework_services.views.utils.get_class_attr` to retrieve them as the original
  unbound callable when needed.
- `DataclassSerializer` cannot deduce field types from `Any` — services that take a
  dataclass for `data` must annotate every field concretely.
