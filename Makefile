.PHONY: help init test lint lint-fix format format-check type-check deps-bump docs-serve docs-build release-bump release-publish release-publish-prepare release-publish-finalize

help:
	@echo "Available targets:"
	@echo "  init             Sync deps (all groups) and install pre-commit hooks"
	@echo "  test             Run pytest with coverage (100% required)"
	@echo "  lint             Run ruff check + ty check"
	@echo "  lint-fix         Auto-fix lint issues with ruff"
	@echo "  format           Format with ruff"
	@echo "  format-check     Verify formatting"
	@echo "  type-check       Run ty over the package"
	@echo "  deps-bump        Upgrade pinned dependencies"
	@echo "  docs-serve       Live-reload docs at http://localhost:8000 (needs mkdocs.yml)"
	@echo "  docs-build       Build docs into ./site (strict — fails on broken links)"
	@echo "  release-bump     Bump version files + CHANGELOG. Usage: make release-bump VERSION=X.Y.Z"
	@echo "  release-publish  prepare → uv publish → finalize (workstation release)"
	@echo "  release-publish-prepare   Run by release.yml on push to main (no-op unless bumped)"
	@echo "  release-publish-finalize  Tag vX.Y.Z + create GitHub Release after PyPI publish"

init:
	uv sync --all-groups
	uv run pre-commit install

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ty check rest_framework_mcp

lint-fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check --diff .

type-check:
	uv run ty check rest_framework_mcp

deps-bump:
	uvx uv-upx upgrade run --profile with_pinned

docs-serve:
	uv run --group docs mkdocs serve

docs-build:
	uv run --group docs mkdocs build --strict

release-bump:
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make release-bump VERSION=X.Y.Z"; exit 1; \
	fi
	uvx bump-my-version bump --new-version "$(VERSION)" patch
	@echo ""
	@echo "Bumped to $(VERSION). Edit CHANGELOG.md to fill the new section,"
	@echo "review with 'git diff', then run 'make release-publish'."

# Release pipeline. The version lives in rest_framework_mcp/version.py
# (pyproject pulls it in via [tool.hatch.version] dynamic). The three targets
# below wrap scripts/release-publish.sh, which is the single source of truth
# for the flow and stays byte-identical across the services + mcp-server repos.
#
#   release-publish-prepare   — version short-circuit, pytest, uv build, extract
#                               CHANGELOG section. Called by release.yml on every
#                               push to main; no-ops unless the version was
#                               bumped past the most recent vX.Y.Z tag.
#   release-publish-finalize  — tag vX.Y.Z, push it, create the GitHub Release.
#                               Called after PyPI publish succeeds in CI.
#   release-publish           — prepare → uv publish → finalize. For end-to-end
#                               workstation releases. Set DRY_RUN=1 to rehearse.
RELEASE_PACKAGE_NAME := djangorestframework-mcp-server
RELEASE_VERSION_FILES := rest_framework_mcp/version.py|^__version__[^=]*= *

release-publish:
	@PACKAGE_NAME='$(RELEASE_PACKAGE_NAME)' \
	VERSION_FILES="$$(printf '$(RELEASE_VERSION_FILES)')" \
		bash scripts/release-publish.sh all

release-publish-prepare:
	@PACKAGE_NAME='$(RELEASE_PACKAGE_NAME)' \
	VERSION_FILES="$$(printf '$(RELEASE_VERSION_FILES)')" \
		bash scripts/release-publish.sh prepare

release-publish-finalize:
	@PACKAGE_NAME='$(RELEASE_PACKAGE_NAME)' \
	VERSION_FILES="$$(printf '$(RELEASE_VERSION_FILES)')" \
		bash scripts/release-publish.sh finalize
