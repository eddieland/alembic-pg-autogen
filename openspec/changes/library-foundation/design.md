## Context

This project is an empty scaffold generated from the `simple-modern-uv` template. It has a placeholder module (`alembic_pg_autogen.py` with a no-op `main()`), a placeholder test (`assert True`), template boilerplate README, and no runtime dependencies. The build system (hatchling + uv-dynamic-versioning), CI matrix (Python 3.11–3.14), linting (ruff, basedpyright, codespell), and Makefile are already in place and working.

The goal is to establish the foundation for a real Alembic autogenerate extension for PostgreSQL — adding the right dependencies, structuring the module for Alembic's extension points, standing up Docker-based integration testing, and replacing template docs with real project documentation — without implementing any autogen logic yet.

## Goals / Non-Goals

**Goals:**

- Add runtime dependencies: `alembic`, `sqlalchemy>=2` (no PostgreSQL driver — users bring their own)
- Require Python >= 3.10, SQLAlchemy 2.x only
- Structure `src/alembic_pg_autogen/` as a proper Alembic autogenerate extension module
- Establish Docker-based integration testing with ephemeral PostgreSQL using `testcontainers`
- Organize tests following the testing pyramid (unit > integration)
- Replace template README and docs with real project documentation
- Keep the library importable and all existing checks (lint, type-check, test) passing

**Non-Goals:**

- Implementing any actual autogeneration logic (that comes in future changes)
- Publishing to PyPI
- Supporting databases other than PostgreSQL
- Adding CLI commands beyond what Alembic already provides
- Setting up end-to-end tests (integration tests with real PostgreSQL are sufficient for foundation)

## Decisions

### 1. Python >= 3.10 and SQLAlchemy 2.x only

Require `python >= 3.10` (lowered from the template's 3.11) and `sqlalchemy >= 2`. SQLAlchemy 1.x is not supported.

- SQLAlchemy 2.x has a substantially different API (2.0-style `select()`, `Session.execute()`, type stubs). Supporting both 1.x and 2.x adds significant complexity for no benefit in a new library.
- Python 3.10 is the minimum to broaden adoption while still having modern features (structural pattern matching, `ParamSpec`, `|` union syntax).
- CI matrix updated to test 3.10–3.14.
- **Alternative considered**: Python 3.11+ (template default) — 3.10 is still widely used and there's no 3.11-only feature we need.

### 2. PostgreSQL driver: user-provided (not a runtime dependency)

This is a library — users already have Alembic and SQLAlchemy configured with their preferred PostgreSQL driver. We do NOT bundle a driver as a runtime dependency.

- Users may use psycopg3, psycopg2, asyncpg, or any other SQLAlchemy-compatible PostgreSQL dialect
- For our own dev/test needs, we use `psycopg[binary]` as a dev dependency (testcontainers integration tests)
- **Alternative considered**: Making `psycopg[binary]` a runtime dependency — unnecessarily opinionated for a library; forces a driver choice on users who may already have one

### 3. Integration testing: `testcontainers`

Use the `testcontainers` Python package to spin up ephemeral PostgreSQL containers in tests.

- Provides a clean, isolated database per test session with zero manual Docker setup
- Well-maintained, widely used in the Python ecosystem
- Tests can run locally and in CI without service container configuration
- CI needs Docker available (GitHub Actions ubuntu runners have it by default)
- **Alternative considered**: Docker Compose + pytest fixtures — more manual plumbing, harder to keep isolated per-session
- **Alternative considered**: GitHub Actions `services:` — ties testing to CI, can't run integration tests locally the same way

### 4. Test organization: marker-based separation

Use pytest markers (`@pytest.mark.integration`) rather than separate directories to distinguish test tiers.

- Keeps test files alongside the code they test (or in `tests/`) without a complex directory hierarchy
- `make test` runs all tests by default; `pytest -m "not integration"` for fast unit-only runs
- A `conftest.py` in `tests/` provides shared fixtures (e.g., the PostgreSQL container)
- **Alternative considered**: Separate `tests/unit/` and `tests/integration/` directories — adds structure that isn't needed yet with a small test surface

### 5. Module structure: minimal extension skeleton

Create the minimum module layout that Alembic autogenerate extensions need:

```
src/alembic_pg_autogen/
  __init__.py          # Public API re-exports
  _compare.py          # Future home of compare functions (empty/stub)
  _render.py           # Future home of render functions (empty/stub)
  _ops.py              # Future home of custom MigrateOperation subclasses (empty/stub)
```

- Alembic autogenerate works via three extension points: comparators (detect diffs), operations (represent diffs), and renderers (emit migration code). Separate modules for each keeps concerns clear.
- Private modules (underscore-prefixed) with public API re-exported from `__init__.py`
- Remove the placeholder `alembic_pg_autogen.py` and `main()` / script entry point — this is a library, not a CLI tool
- **Alternative considered**: Single-file module — would quickly become unwieldy as autogen logic grows
- **Alternative considered**: Deeper package nesting (e.g., `autogen/comparators/`) — premature; start flat and restructure if needed

### 6. Remove the script entry point

The `[project.scripts]` section currently defines `alembic-pg-autogen = "alembic_pg_autogen:main"`. This is template boilerplate — this project is a library extension for Alembic, not a standalone CLI tool. Remove it.

### 7. Documentation approach

Replace template docs with:

- **README.md**: Project description, installation, basic usage (how to configure Alembic to use this extension), development setup
- Keep the existing `docs/` guides (installation.md, development.md) but update them to be project-specific rather than template boilerplate
- No separate API docs generation yet — the codebase is too small

### 8. Makefile updates

Add a `test-unit` target for running only unit tests (excludes integration marker). Keep `test` as the full suite including integration tests.

## Risks / Trade-offs

**testcontainers requires Docker** → Developers and CI environments must have Docker installed. Mitigation: Document this requirement clearly; GitHub Actions ubuntu runners include Docker by default. Unit tests (marked separately) can always run without Docker.

**Empty stub modules may confuse contributors** → Modules with no real code could look like dead code. Mitigation: Brief module-level comments explaining their future purpose.

**Integration tests add CI time** → Pulling and starting a PostgreSQL container takes time. Mitigation: testcontainers caches images; the overhead is typically 5-10 seconds. Acceptable for a foundation that enables real database testing.
