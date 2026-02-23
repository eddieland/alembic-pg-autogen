# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

alembic-pg-autogen is an early-stage Python package (currently placeholder). It uses **uv** as the package manager,
**hatchling** as the build backend, and **uv-dynamic-versioning** for git-tag-based versioning.

Always use Context7 MCP when I need library/API documentation, code generation, setup or configuration steps without me
having to explicitly ask.

## Commands

```bash
make install     # uv sync --all-extras
make lint        # Runs mdformat, codespell, ruff check --fix, ruff format, basedpyright --stats
make test        # uv run pytest
make build       # uv build
make upgrade     # uv sync --upgrade --all-extras --dev
make clean       # Remove build artifacts, caches, .venv
```

Run a single test:

```bash
uv run pytest tests/test_placeholder.py
uv run pytest -k "test_name"
```

## Code Layout

- `src/alembic_pg_autogen/` — Package source (src layout)
- `tests/` — Pytest tests (also discovers tests in `src/`)

## Code Style & Tooling

- **Line length**: 120 characters for all files (code, markdown, etc.).
- **Ruff**: Line length 120. Lints for errors, style, import ordering, modern Python idioms, bug-prone patterns,
  docstrings (Google convention), and logging format. **Wildcard (`*`) imports are banned** — always use explicit
  imports. All public modules/functions/classes in `src/` require docstrings; tests are exempt.
- **BasedPyright**: Type checker. Configured in pyproject.toml with several strict rules relaxed (reportAny,
  reportUnusedCallResult, etc.).
- **mdformat**: Markdown formatter (wrap 120, LF line endings). Plugins: mdformat-gfm (GitHub Flavored Markdown),
  mdformat-pyproject (config from pyproject.toml), mdformat-ruff (formats Python code blocks). Runs on `make fmt` and as
  a pre-commit hook.
- **Codespell**: Spell checking on src, tests, docs, and markdown files.
- Lint auto-fixes on run (`--fix`, `--write-changes`); running `make lint` modifies files in place.
- **Module ordering**: Public API functions first, `_private` helpers after, generally in order of usefulness to someone
  reading the module. Do not use visual fences/separators (e.g. `# ---- Private helpers ----`) to demarcate sections.
- **Prefer `NamedTuple` over `dataclass`**: Use `typing.NamedTuple` for data containers wherever possible — they are
  much cheaper to construct than frozen dataclasses and are immutable by default.
- **Use `if TYPE_CHECKING:` guards**: Import types used only for annotations inside `if TYPE_CHECKING:` blocks to
  minimize runtime import cost.
- **Prefer `Final` and immutable collection types**: Use `Final` for module-level and instance constants. Annotate
  collections with immutable types (`Mapping` over `dict`, `Sequence` over `list`, `AbstractSet` over `set`) unless
  mutation is intended.

## Python Version

Requires Python >=3.10, \<4.0. CI tests against 3.10–3.14.

## OpenSpec

This project uses **OpenSpec** for spec-driven changes. When implementing code, check for active changes in
`openspec/changes/` — these contain proposals, design docs, specs, and task lists that describe what to build and how.

- `openspec/specs/` — Main specification files (source of truth for the project's design)
- `openspec/changes/` — Active changes with artifacts (proposal, design, delta specs, tasks)
- `openspec/changes/archive/` — Completed changes
- `openspec/config.yaml` — OpenSpec configuration

When working on an OpenSpec change, read all context files (proposal, design, specs, tasks) before implementing. Keep
changes minimal and scoped to each task. Mark tasks complete (`- [ ]` → `- [x]`) as you finish them.
