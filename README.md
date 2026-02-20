# alembic-pg-autogen

[![CI](https://github.com/eddie-on-gh/alembic-pg-autogen/actions/workflows/ci.yml/badge.svg)](https://github.com/eddie-on-gh/alembic-pg-autogen/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)
[![Python](https://img.shields.io/pypi/pyversions/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Status: Alpha** — the core pipeline works and is tested against real PostgreSQL, but the API may change before 1.0.

Alembic autogenerate extension for PostgreSQL functions and triggers. If you've been manually writing `op.execute()`
calls every time you add or change a PL/pgSQL function, this package automates that — declare your DDL strings and let
`alembic revision --autogenerate` figure out the `CREATE`, `DROP`, and `CREATE OR REPLACE` for you.

## Background

[alembic_utils](https://github.com/olirice/alembic_utils) pioneered autogenerate support for PostgreSQL objects and has
been hugely helpful to the community. This project takes a different approach aimed at faster performance on large
schemas with many functions and triggers.

## How it works

You declare your desired functions and triggers as plain DDL strings. When you run `alembic revision --autogenerate`,
the extension:

1. **Inspects** the live database catalog for existing functions and triggers
1. **Canonicalizes** your DDL by executing it inside a savepoint and reading back the catalog (then rolling back)
1. **Diffs** current vs. desired state, matching objects by identity (schema + name + argument types)
1. **Emits** migration ops in dependency-safe order (drop triggers before functions, create functions before triggers)

## Installation

```bash
pip install alembic-pg-autogen
```

Requires Python 3.10+ and SQLAlchemy 2.x. Bring your own PostgreSQL driver (`psycopg`, `psycopg2`, `asyncpg`, etc.).

## Quick start

### 1. Declare your DDL

In your `env.py` (or a separate module), define the functions and triggers you want managed:

```python
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

### 2. Wire it into `env.py`

Import the package (this registers the Alembic comparator plugin). Then in your `run_migrations_online()` function, pass
them as keyword arguments to `context.configure()` along with the `autogenerate_plugins` list::

```python
import alembic_pg_autogen  # noqa: F401  # registers the comparator plugin

# ... in run_migrations_online():
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
    pg_functions=PG_FUNCTIONS,
    pg_triggers=PG_TRIGGERS,
)
```

### 3. Autogenerate as usual

```bash
alembic revision --autogenerate -m "add audit trigger"
```

### 4. Generated migration

The migration file will contain `op.execute()` calls — no custom op imports needed:

```python
def upgrade() -> None:
    op.execute("""CREATE OR REPLACE FUNCTION public.audit_trigger_func()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $function$""")
    op.execute("""CREATE TRIGGER set_updated_at BEFORE UPDATE ON public.my_table
 FOR EACH ROW EXECUTE FUNCTION audit_trigger_func()""")


def downgrade() -> None:
    op.execute("DROP TRIGGER set_updated_at ON public.my_table")
    op.execute("DROP FUNCTION public.audit_trigger_func()")
```

Note that the `upgrade` DDL is the **canonical** form read back from PostgreSQL's catalog, not a copy of your input.
This means formatting will differ from what you wrote, but the semantics are identical.

## Migrating from alembic_utils

If you're coming from alembic_utils, you can pass your existing `PGFunction` / `PGTrigger` objects directly — any object
with a `to_sql_statement_create()` method is accepted alongside plain DDL strings:

```python
from alembic_utils.pg_function import PGFunction

my_func = PGFunction(schema="public", signature="my_func()", definition="...")

PG_FUNCTIONS = [
    my_func,  # alembic_utils object — works as-is
    "CREATE FUNCTION new_func() ...",  # plain DDL string — also works
]
```

This lets you migrate incrementally without rewriting all your declarations at once.

## Development

```bash
make install     # Install dependencies (uses uv)
make lint        # Format (mdformat, codespell, ruff) then type-check (basedpyright)
make test        # Run full test suite (requires Docker for integration tests)
make test-unit   # Run unit tests only (no Docker needed)
```

## License

MIT
