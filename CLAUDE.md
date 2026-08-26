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

- `src/alembic_pg_autogen/`: package source (src layout)
- `tests/`: pytest tests (pytest also discovers tests in `src/`)

## Code Style & Tooling

- **Line length**: 120 characters for all files (code, markdown, etc.).
- **Ruff**: Line length 120. Lints for errors, style, import ordering, modern Python idioms, bug-prone patterns,
  docstrings (Google convention), and logging format. **Wildcard (`*`) imports are banned**. Always use explicit
  imports. All public modules/functions/classes in `src/` require docstrings; tests are exempt.
- **BasedPyright**: Type checker. Configured in pyproject.toml with several strict rules relaxed (reportAny,
  reportUnusedCallResult, etc.).
- **mdformat**: Markdown formatter (wrap 120, LF line endings). Plugins: mdformat-gfm (GitHub Flavored Markdown),
  mdformat-pyproject (config from pyproject.toml), mdformat-ruff (formats Python code blocks). Runs on `make fmt` and as
  a prek hook.
- **Always run mdformat after changing any markdown file**: `README.md`, `CLAUDE.md`, and anything under `openspec/` or
  `docs/`. Run `make fmt` (or `uv run mdformat README.md CLAUDE.md openspec/`) and confirm with
  `uv run mdformat --check README.md CLAUDE.md openspec/`, which is what CI gates on: a bullet wrapped short of 120
  characters is enough to fail the build. The `PostToolUse` hook in `.claude/settings.json` formats markdown
  automatically, but only for files written with the Edit/Write tools. Markdown written through the shell (heredocs,
  `sed`, scripts) bypasses the hook. Re-run the check after any late edit; ticking off a task list counts.
- **Codespell**: Spell checking on src, tests, docs, and markdown files.
- Lint auto-fixes on run (`--fix`, `--write-changes`); running `make lint` modifies files in place.
- **Module ordering**: Public API functions first, `_private` helpers after, generally in order of usefulness to someone
  reading the module. Do not use visual fences/separators (e.g. `# ---- Private helpers ----`) to demarcate sections.
- **Prefer `NamedTuple` over `dataclass`**: Use `typing.NamedTuple` for data containers wherever possible. They are much
  cheaper to construct than frozen dataclasses, and they are immutable by default.
- **Use `if TYPE_CHECKING:` guards**: Import types used only for annotations inside `if TYPE_CHECKING:` blocks to
  minimize runtime import cost.
- **Prefer `Final` and immutable collection types**: Use `Final` for module-level and instance constants. Annotate
  collections with immutable types (`Mapping` over `dict`, `Sequence` over `list`, `AbstractSet` over `set`) unless
  mutation is intended.

## Documentation Style

These rules apply to the prose you write. The scope covers documentation, `README.md`, docstrings, and code comments.
The scope also covers OpenSpec artifacts, commit messages, and pull request titles and bodies. OpenSpec artifacts
include proposals, design docs, specs, and task lists.

The rules apply to this file too. Older prose in this repository predates the rules. Rewrite that prose to this style
when you edit it.

The style follows the principles of ASD-STE100 (simplified technical English), applied informally. The rules below are
authoritative. Write for readers whose first language is not English.

Required:

- Write one idea per sentence. Keep each sentence under 20 words.
- Use active voice and present tense.
- Use the same term for the same concept every time.
- Use concrete nouns. Name the file, service, table, or function.

Not allowed:

- Dashes (em dash, en dash, or hyphen) as punctuation between clauses. Use a period, a comma, or parentheses instead.
- Idioms. Write the literal meaning. A non-native speaker may not recognize the idiom.
- Phrasal verbs. Write "start" instead of "spin up". Write "remove" instead of "tear down". Write "deploy" instead of
  "roll out". Write "use" instead of "fall back on".
- Evaluative adjectives: robust, elegant, seamless, comprehensive, powerful, significant, critical, clean.
- The patterns "not just X, but Y" and "it's not X, it's Y".
- Lists of three items written for rhythm instead of content.
- Openers that delay the point: "This ticket aims to", "In order to", "As part of our ongoing effort".

## Python Version

Requires Python >=3.10, \<4.0. CI tests against 3.10–3.14.

## OpenSpec

This project uses **OpenSpec** for spec-driven changes. When implementing code, check for active changes in
`openspec/changes/`. These directories contain proposals, design docs, specs, and task lists that describe what to build
and how.

- `openspec/specs/`: main specification files (source of truth for the project's design)
- `openspec/changes/`: active changes with artifacts (proposal, design, delta specs, tasks)
- `openspec/changes/archive/`: completed changes
- `openspec/config.yaml`: OpenSpec configuration

When working on an OpenSpec change, read all context files (proposal, design, specs, tasks) before implementing. Keep
changes minimal and scoped to each task. Mark tasks complete (`- [ ]` → `- [x]`) as you finish them.
