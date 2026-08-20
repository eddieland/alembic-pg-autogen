# alembic-pg-autogen

[![PyPI](https://img.shields.io/pypi/v/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)
[![Python](https://img.shields.io/pypi/pyversions/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/eddieland/alembic-pg-autogen/ci.yml?label=CI)](https://github.com/eddieland/alembic-pg-autogen/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/eddieland/alembic-pg-autogen/graph/badge.svg)](https://codecov.io/gh/eddieland/alembic-pg-autogen)
[![Docs](https://readthedocs.org/projects/alembic-pg-autogen/badge/?version=latest)](https://alembic-pg-autogen.readthedocs.io)
[![Downloads](https://img.shields.io/pypi/dm/alembic-pg-autogen)](https://pypi.org/project/alembic-pg-autogen/)

> **Status: Beta** — the core pipeline is stable and tested against real PostgreSQL. The API may still evolve before
> 1.0, but the library is suitable for production use.

Alembic autogenerate extension for PostgreSQL functions and triggers. Declare your DDL strings and let
`alembic revision --autogenerate` figure out the `CREATE`, `DROP`, and `CREATE OR REPLACE` for you.

<p align="center">
  <img src="https://raw.githubusercontent.com/eddieland/alembic-pg-autogen/main/docs/logo.png" width="350" alt="alembic-pg-autogen logo"/>
</p>

## Background

[alembic_utils](https://github.com/olirice/alembic_utils) pioneered autogenerate support for PostgreSQL objects and has
been hugely helpful to the community. This project takes a different approach aimed at faster performance on large
schemas with many functions and triggers.

## How it works

You declare your desired functions and triggers as plain DDL strings. At autogenerate time, the extension inspects the
live database catalog, canonicalizes your DDL via a temporary savepoint, diffs current vs. desired state, and emits
migration ops in dependency-safe order.

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

`autogenerate_plugins` *replaces* Alembic's default of `["alembic.autogenerate.*"]` rather than adding to it, so the
built-in wildcard has to stay in the list next to ours. This package only ever adds comparators; it never substitutes
for Alembic's own, which still do all the table, column, index, and constraint work.

Getting the list wrong fails silently in one of two directions, and neither raises an error:

- **Omitting `alembic_pg_autogen.*`** (or not setting the option at all) leaves the default in force. Importing the
  package registers the plugins but does not opt them in, so functions, triggers, views, and check constraint
  expressions are simply never compared.
- **Omitting `alembic.autogenerate.*`** turns autogenerate into a total no-op — not just for Alembic's features but for
  ours too. The comparator that drives the entire diff belongs to `alembic.autogenerate.schemas`, so with it excluded
  nothing dispatches and every migration comes out empty.

To confirm what a run actually loaded, set `logging.getLogger("alembic.runtime.plugins").setLevel(logging.INFO)`. A
correct configuration logs nine `setting up autogenerate plugin ...` lines: Alembic's seven, plus
`alembic_pg_autogen.compare` and `alembic_pg_autogen.checkconstraints`.

## What gets managed

An object type becomes *managed* when you declare it. Within a managed type the declared set is the whole truth, so
objects found in the inspected schemas that you did not declare are dropped — that is what makes the tool declarative.

A key you never pass leaves that object type alone entirely: nothing is inspected, diffed, or emitted for it. The
example above declares functions and triggers, so views are untouched, and adopting the library one object type at a
time is safe.

To record the opt-out at the configuration site — or to set it conditionally — pass the `IGNORED` sentinel, which means
exactly what omitting the key means:

```python
from alembic_pg_autogen import IGNORED

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
    pg_functions=PG_FUNCTIONS,
    pg_triggers=PG_TRIGGERS,
    pg_views=IGNORED,  # not ready to manage views yet — don't drop them
)
```

An empty list is a different thing again: `pg_views=[]` declares "there should be no views" and drops every existing
one, while `pg_views=IGNORED` declares "views are not managed here". Watch for this if you build the list dynamically —
`collect_view_ddl() or IGNORED` keeps an empty result from clearing your schema.

Because an unrecognized key leaves its type unmanaged, a misspelled one (`pg_view` for `pg_views`) would quietly manage
nothing; those are reported as a warning naming the key you meant.

## Check constraints

Alembic detects when a named `CHECK` constraint is added to or removed from your models, but two constraints that share
a name are always presumed equivalent — normalizing SQL expressions across backends is not something Alembic can do. So
tightening `amount >= 0` to `amount > 0` in a model generates nothing, and the schema drifts.

This package closes that gap for PostgreSQL — and **closing the gap is all it does**. Our comparator augments Alembic's
rather than superseding it, so `alembic.autogenerate.checkconstraint_byname` stays enabled and keeps its job:

| Situation                                           | Who handles it                                | Result                          |
| --------------------------------------------------- | --------------------------------------------- | ------------------------------- |
| Name in your models only                            | `alembic.autogenerate.checkconstraint_byname` | `create_check_constraint`       |
| Name in the database only                           | `alembic.autogenerate.checkconstraint_byname` | `drop_constraint`               |
| Name on **both** sides, expression possibly changed | `alembic_pg_autogen.checkconstraints`         | `drop_constraint` + re-`create` |

The two sets are disjoint by construction, so no operation is ever emitted twice, and there is no duplication to be
avoided by turning Alembic's comparator off. Disabling it is a pure loss: added and removed constraints stop being
detected at all, while ours contributes nothing in their place. Alembic's own expression check is a guaranteed "equal"
on PostgreSQL — `DefaultImpl.compare_check_constraint` returns `Equal()` and the PostgreSQL dialect does not override it
— which is precisely the gap ours fills and the reason the two never collide.

One consequence is worth internalizing: if the only drift in your schema is constraints you forgot to declare and ones
you no longer want, **this package will report nothing**, and every operation in the migration will have come from
Alembic. That is the intended division, not a malfunction.

Keep declaring constraints in SQLAlchemy metadata as usual:

```python
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    amount: Mapped[Decimal]

    __table_args__ = (CheckConstraint("amount > 0", name="ck_orders_amount"),)
```

and a changed expression now produces a migration:

```python
def upgrade() -> None:
    op.drop_constraint("ck_orders_amount", "orders", type_="check")
    op.create_check_constraint("ck_orders_amount", "orders", "amount > 0")
```

Beyond the plugin list above there is nothing to configure. Each expression is round-tripped through PostgreSQL — added
to the table as a throwaway `NOT VALID` constraint inside a savepoint that is rolled back — so `amount >= 0` and the
catalog's `amount >= 0::numeric` are recognized as the same constraint, and a real change is recognized as a real
change.

## Installation

```bash
pip install alembic-pg-autogen
```

Requires Python 3.10+ and SQLAlchemy 2.x. Bring your own PostgreSQL driver (`psycopg`, `psycopg2`, `asyncpg`, etc.).
This package depends on [postgast](https://github.com/eddieland/postgast) for DDL parsing, which requires
`protobuf >= 5.27`.

## Documentation

Full documentation is available at [alembic-pg-autogen.readthedocs.io](https://alembic-pg-autogen.readthedocs.io),
including a quick-start guide, migration instructions for alembic_utils users, and API reference.

## Development

```bash
make install     # Install dependencies (uses uv)
make lint        # Format (mdformat, codespell, ruff) then type-check (basedpyright)
make test        # Run full test suite (requires Docker for integration tests)
make test-unit   # Run unit tests only (no Docker needed)
```

## License

[MIT](LICENSE)
