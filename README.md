# alembic-pg-autogen

An Alembic autogenerate extension for PostgreSQL. Extends Alembic's `--autogenerate` to detect and emit migrations for PostgreSQL-specific objects that Alembic doesn't handle out of the box.

> **Note:** This project is in early development. The capabilities described below are aspirational and not yet implemented.

## Goals

- **PostgreSQL-native autogeneration** — Detect diffs and generate migration code for PostgreSQL objects beyond what Alembic covers by default (e.g., custom types, extensions, policies, and other DDL).
- **Seamless Alembic integration** — Plug into Alembic's existing autogenerate pipeline via its standard extension points (comparators, operations, renderers), so `alembic revision --autogenerate` just works.
- **No new CLI** — This is a library extension, not a standalone tool. It enhances Alembic rather than replacing or wrapping it.
- **PostgreSQL only** — Focused entirely on PostgreSQL. No multi-database abstraction layer.

## Installation

```bash
pip install alembic-pg-autogen
```

Requires Python 3.11+.

## Development

```bash
make install     # Install dependencies (uses uv)
make lint        # Run codespell, ruff, basedpyright
make test        # Run full test suite (requires Docker for integration tests)
make test-unit   # Run unit tests only (no Docker needed)
```

Integration tests use [testcontainers](https://testcontainers-python.readthedocs.io/) to spin up ephemeral PostgreSQL instances, so Docker must be available for the full test suite.
