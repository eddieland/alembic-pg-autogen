# alembic-pg-autogen

[![CI](https://github.com/eddie-on-gh/alembic-pg-autogen/actions/workflows/ci.yml/badge.svg)](https://github.com/eddie-on-gh/alembic-pg-autogen/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)
[![Python](https://img.shields.io/pypi/pyversions/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Alembic autogenerate extension for PostgreSQL. Extends Alembic's `--autogenerate` to detect and emit migrations for
PostgreSQL functions and triggers that Alembic doesn't handle out of the box.

## How it works

You declare your desired functions and triggers as DDL strings. When you run `alembic revision --autogenerate`, the
extension:

1. **Inspects** the current database catalog (`pg_proc`, `pg_trigger`)
1. **Canonicalizes** your DDL by executing it in a savepoint and reading back the catalog (then rolling back)
1. **Diffs** current vs. desired state, matching objects by identity
1. **Emits** `CREATE`, `DROP`, or `CREATE OR REPLACE` operations in dependency-safe order (drop triggers before
   functions, create functions before triggers)

## Installation

```bash
pip install alembic-pg-autogen
```

Requires Python 3.10+ and SQLAlchemy 2.x. You provide your own PostgreSQL driver (psycopg, psycopg2, asyncpg, etc.).

## Usage

In your `env.py`, import the extension and pass your DDL via `process_revision_directives` options:

```python
import alembic_pg_autogen  # noqa: F401  # registers the comparator plugin

# Define your functions and triggers as DDL strings
PG_FUNCTIONS = [
    """
    CREATE OR REPLACE FUNCTION audit_trigger_func()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$
    """,
]

PG_TRIGGERS = [
    """
    CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON my_table
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_func()
    """,
]
```

Then in your `run_migrations_online()` function, pass them as context options:

```python
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    opts={
        "pg_functions": PG_FUNCTIONS,
        "pg_triggers": PG_TRIGGERS,
    },
)
```

Run autogenerate as usual:

```bash
alembic revision --autogenerate -m "add audit trigger"
```

The generated migration will contain `op.execute()` calls with the appropriate `CREATE`, `DROP`, or `CREATE OR REPLACE`
statements.

## Development

```bash
make install     # Install dependencies (uses uv)
make lint        # Format (mdformat, codespell, ruff) then type-check (basedpyright)
make test        # Run full test suite (requires Docker for integration tests)
make test-unit   # Run unit tests only (no Docker needed)
```

## License

MIT
