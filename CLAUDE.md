# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

alembic-pg-autogen is an early-stage Python package (currently placeholder). It uses **uv** as the package manager, **hatchling** as the build backend, and **uv-dynamic-versioning** for git-tag-based versioning.

## Commands

```bash
make install     # uv sync --all-extras
make lint        # Runs codespell, ruff check --fix, ruff format, basedpyright --stats
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

- **Ruff**: Line length 120. Enabled rule sets: E, F, UP, B, I. E501 (line-too-long) is ignored.
- **BasedPyright**: Type checker. Configured in pyproject.toml with several strict rules relaxed (reportAny, reportUnusedCallResult, etc.).
- **Codespell**: Spell checking on src, tests, and README.md.
- Lint auto-fixes on run (`--fix`, `--write-changes`); running `make lint` modifies files in place.

## Python Version

Requires Python >=3.11, <4.0. CI tests against 3.11–3.14.

## OpenSpec

This project uses **OpenSpec** for spec-driven changes. When implementing code, check for active changes in `openspec/changes/` — these contain proposals, design docs, specs, and task lists that describe what to build and how.

- `openspec/specs/` — Main specification files (source of truth for the project's design)
- `openspec/changes/` — Active changes with artifacts (proposal, design, delta specs, tasks)
- `openspec/changes/archive/` — Completed changes
- `openspec/config.yaml` — OpenSpec configuration

When working on an OpenSpec change, read all context files (proposal, design, specs, tasks) before implementing. Keep changes minimal and scoped to each task. Mark tasks complete (`- [ ]` → `- [x]`) as you finish them.
