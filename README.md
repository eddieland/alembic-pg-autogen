# alembic-pg-autogen

[![PyPI](https://img.shields.io/pypi/v/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)
[![Python](https://img.shields.io/pypi/pyversions/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/eddieland/alembic-pg-autogen/ci.yml?label=CI)](https://github.com/eddieland/alembic-pg-autogen/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/eddieland/alembic-pg-autogen/graph/badge.svg)](https://codecov.io/gh/eddieland/alembic-pg-autogen)
[![Docs](https://readthedocs.org/projects/alembic-pg-autogen/badge/?version=latest)](https://alembic-pg-autogen.readthedocs.io)
[![Downloads](https://img.shields.io/pypi/dm/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)

> **Status: Beta.** The core pipeline is stable. The test suite runs against a real PostgreSQL server. The API may still
> change before version 1.0. You can use the library in production.

Alembic autogenerate extension for PostgreSQL functions and triggers. You declare your DDL strings, and
`alembic revision --autogenerate` writes the `CREATE`, `DROP`, and `CREATE OR REPLACE` statements.

<p align="center">
  <img src="https://raw.githubusercontent.com/eddieland/alembic-pg-autogen/main/docs/logo.png" width="350" alt="alembic-pg-autogen logo"/>
</p>

## Background

[alembic_utils](https://github.com/olirice/alembic_utils) was the first project to add autogenerate support for
PostgreSQL objects. Many projects use it today. This project takes a different approach. The goal is faster autogenerate
on large schemas that contain many functions and triggers.

## How it works

You declare each function and trigger as a plain DDL string. Autogenerate then runs four steps:

1. The extension reads the live database catalog.
1. The extension canonicalizes your DDL inside a temporary savepoint.
1. The extension compares the current state against the declared state.
1. The extension emits migration operations in dependency order.

## Quick example

```python
import alembic_pg_autogen  # noqa: F401  # registers the comparator plugin

PG_FUNCTIONS = [
    """
    CREATE OR REPLACE FUNCTION set_updated_at()
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
    CREATE TRIGGER set_updated_at_on_update
    BEFORE UPDATE ON my_table
    FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """,
]

# in run_migrations_online():
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
    pg_functions=PG_FUNCTIONS,
    pg_triggers=PG_TRIGGERS,
)
```

```bash
alembic revision --autogenerate -m "add audit function and trigger"
```

### Both wildcards are required

`autogenerate_plugins` *replaces* Alembic's default value of `["alembic.autogenerate.*"]`. The option does not extend
the default value. Your list must keep the built-in wildcard next to ours. This package only adds comparators. This
package never replaces Alembic's comparators, which still handle every table, column, index, and constraint.

A wrong list fails silently in two directions. Neither direction raises an error:

- **Omitting `alembic_pg_autogen.*`** keeps Alembic's default value in force. The same result follows when you never set
  the option. An import of the package registers the plugins but does not enable them. The extension then never compares
  functions, triggers, views, or check constraint expressions.
- **Omitting `alembic.autogenerate.*`** makes every migration empty. This result covers Alembic's own features and this
  package's features. The comparator that drives the whole diff belongs to `alembic.autogenerate.schemas`. Alembic
  dispatches no comparator without it.

To confirm what a run loaded, set `logging.getLogger("alembic.runtime.plugins").setLevel(logging.INFO)`. Each included
plugin logs one `setting up autogenerate plugin ...` line. A correct configuration logs lines from **both** namespaces.
It logs several `alembic.autogenerate.*` entries, above all `schemas` and `tables`, which drive the diff. It also logs
`alembic_pg_autogen.compare` and `alembic_pg_autogen.checkconstraints`. Lines from one namespace only mean that your
list is missing the other wildcard.

## What gets managed

An object type becomes *managed* when you declare it. Inside a managed type, your declared set is the whole truth. The
extension drops each object that it finds in the inspected schemas and that you did not declare. This rule is what makes
the tool declarative.

A key that you never pass leaves that object type unmanaged. The extension inspects nothing, compares nothing, and emits
nothing for that type. The example above declares functions and triggers, so views stay unchanged. You can adopt the
library one object type at a time.

Pass the `IGNORED` sentinel to record the unmanaged object type in the configuration, or to set it conditionally. The
sentinel means exactly what an absent key means:

```python
from alembic_pg_autogen import IGNORED

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
    pg_functions=PG_FUNCTIONS,
    pg_triggers=PG_TRIGGERS,
    pg_views=IGNORED,  # views are not managed yet, so keep them
)
```

An empty list means something else. `pg_views=[]` declares that the schema contains no views, and the extension drops
every existing view. `pg_views=IGNORED` declares that this configuration does not manage views. Note the difference when
you build the list at runtime. The expression `collect_view_ddl() or IGNORED` keeps an empty result from clearing your
schema.

An unrecognized key also leaves its object type unmanaged. A misspelled key such as `pg_view` therefore manages nothing.
The extension reports each misspelled key as a warning that names the key you meant.

## Check constraints

Alembic detects a named `CHECK` constraint that you add to your models or remove from them. Alembic treats two
constraints with the same name as equal, because it cannot normalize SQL expressions across backends. A change from
`amount >= 0` to `amount > 0` therefore generates no migration, and the schema drifts.

This package closes that gap for PostgreSQL, and **the gap is all that it closes**. Our comparator augments Alembic's
comparator. Keep `alembic.autogenerate.checkconstraint_byname` enabled, so that it continues its own work:

| Situation                                           | Who handles it                                | Result                                          |
| --------------------------------------------------- | --------------------------------------------- | ----------------------------------------------- |
| Name in your models only                            | `alembic.autogenerate.checkconstraint_byname` | `create_check_constraint`                       |
| Name in the database only                           | `alembic.autogenerate.checkconstraint_byname` | `drop_constraint`                               |
| Name on **both** sides, expression possibly changed | `alembic_pg_autogen.checkconstraints`         | `drop_constraint` and `create_check_constraint` |

The two sets of names are disjoint by construction. No operation is ever emitted twice, so you gain nothing when you
disable Alembic's comparator. You lose the detection of added constraints and removed constraints, and our comparator
reports nothing in their place. On PostgreSQL, Alembic's own expression check always reports equality:
`DefaultImpl.compare_check_constraint` returns `Equal()`, and the PostgreSQL dialect does not override that method. That
result is the gap our comparator fills, and the reason the two comparators never collide.

Remember one consequence. Your schema may drift only through constraints that you forgot to declare and constraints that
you no longer want. This package then reports nothing, and Alembic emits every operation in the migration. That outcome
is the intended division of work.

Declare constraints in SQLAlchemy metadata as usual:

```python
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    amount: Mapped[Decimal]

    __table_args__ = (CheckConstraint("amount > 0", name="ck_orders_amount"),)
```

A changed expression now produces a migration:

```python
def upgrade() -> None:
    op.drop_constraint("ck_orders_amount", "orders", type_="check")
    op.create_check_constraint("ck_orders_amount", "orders", "amount > 0")
```

The plugin list above is the only configuration. The comparator sends each expression through PostgreSQL. It adds the
expression to the table as a throwaway `NOT VALID` constraint. It works inside a savepoint, and it reverts that
savepoint. PostgreSQL therefore reports `amount >= 0` and the catalog form `amount >= 0::numeric` as one constraint. A
real change still appears as a change.

## Installation

```bash
pip install alembic-pg-autogen
```

Requires Python 3.10+ and SQLAlchemy 2.x. Install a PostgreSQL driver yourself (`psycopg`, `psycopg2`, or `asyncpg`).
This package depends on [postgast](https://github.com/eddieland/postgast) for DDL parsing. postgast requires
`protobuf >= 5.27`.

## Documentation

The full documentation is at [alembic-pg-autogen.readthedocs.io](https://alembic-pg-autogen.readthedocs.io). It contains
a quick start guide, migration instructions for alembic_utils users, and the API reference.

## Development

```bash
make install     # Install dependencies (uses uv)
make lint        # Format (mdformat, codespell, ruff) then type-check (basedpyright)
make test        # Run full test suite (requires Docker for integration tests)
make test-unit   # Run unit tests only (no Docker needed)
```

## License

[MIT](LICENSE)
