## Context

The library manages functions, triggers, views, and check constraints through one pipeline. It inspects the catalog,
canonicalizes the desired state by a round trip through PostgreSQL, diffs the two snapshots, and renders `op.execute()`
calls. Desired state arrives as DDL strings. See `proposal.md` for motivation.

Rows in a lookup table fit the same pipeline. The declared rows are the desired state. The table's current rows are the
current state. The only new problem is value equality. A Python `Decimal("1.5")`, a driver-returned `Decimal("1.50")`,
and a catalog `numeric(10, 2)` value must compare as one value without a rule per type.

Every decision below rests on prototype work against PostgreSQL 16.13, SQLAlchemy 2.0.46, psycopg 3.3.3, and Alembic
1.19.1 (2026-09-21). The prototype script is committed next to this document as `prototype.py`. It proved these facts:

1. `value::text` gives one canonical form per value. `jsonb` sorts keys, `boolean` prints `true` and `false`,
   `timestamptz` prints in the session time zone, and `numeric(10, 2)` keeps its scale.
1. `json` has no equality operator. `ROW(...) IS DISTINCT FROM` fails on it. `json::text` works.
1. `CREATE TABLE pg_temp.name (...)` creates a temporary table. A savepoint rollback removes it.
1. A SQLAlchemy `Table` with `schema="pg_temp"` and typed columns adapts Python values through the driver. `dict`
   becomes `jsonb`, `list` becomes an array, `UUID`, `Decimal`, `date`, `datetime`, and `bytes` all round-trip.
1. A quoted literal with no type resolves to the column type in `INSERT`, `UPDATE`, and `WHERE`. No cast is needed.
1. Inserting a value's own `::text` form reads back as the same `::text`. Autogenerate therefore converges.
1. PostgreSQL calls `1.5` and `1.50` equal in an unconstrained `numeric` column. Their text forms differ. `citext`
   behaves the same way. One `UPDATE` makes the stored text match the declared text.
1. `CREATE TEMP TABLE ... (LIKE t INCLUDING DEFAULTS)` copies `nextval()` of the real sequence. An insert into the copy
   advances that sequence, and a savepoint rollback does not restore it.
1. Under `standard_conforming_strings`, doubling single quotes is the only escaping a literal needs.
1. Some numeric text forms are not valid bare literals: `NaN`, `Infinity`, and `$12.34` for `money`.
1. `pg_attribute` reports which declared columns exist. A declared column the catalog lacks reads as `NULL`.
1. `DELETE` of a row that a foreign key references fails with `ForeignKeyViolation`.

## Goals / Non-Goals

**Goals:**

- Declare the rows of a lookup table in code and let autogenerate emit the `INSERT`, `UPDATE`, and `DELETE` statements
- Make `downgrade()` exact. Every operation carries the rows it needs to reverse itself.
- Support tables that only grow. An `expand_only` table never receives a `DELETE`.
- Put the first rows and the `create_table` in the same migration
- Compare values without a rule per type. PostgreSQL decides the canonical form.

**Non-Goals:**

- Tables without a SQLAlchemy `Table` object
- SQL expressions or server-evaluated values in declared rows
- Rows that omit some declared columns
- Semantic equality beyond the text form (see fact 7)
- Large tables. The comparator reads every row of a declared table.
- A strict mode that fails on undeclared rows in an expand-only table

## Decisions

### D1: `DataTable` wraps a SQLAlchemy `Table` and plain row mappings

```python
from alembic_pg_autogen import DataTable

ORDER_STATUS = DataTable(
    OrderStatus.__table__,
    rows=[
        {"code": "new", "label": "New", "sort_order": 10},
        {"code": "paid", "label": "Paid", "sort_order": 20},
        {"code": "shipped", "label": "Shipped", "sort_order": 30},
    ],
    expand_only=True,
)

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
    pg_data_tables=[ORDER_STATUS],
)
```

`DataTable` is a `NamedTuple` with four fields: `table`, `rows`, `key`, and `expand_only`. The `Table` supplies the
schema, the name, the primary key, and the column types. Rows are plain mappings, so a Python `Enum` feeds them in one
expression:

```python
rows = [{"code": m.value, "label": m.name.title(), "sort_order": i * 10} for i, m in enumerate(OrderStatus)]
```

The comparator validates each declaration before it touches the database. A `ValueError` names the table and the
problem. The checks are: every row has the same column set, every column exists on the `Table`, the key is a subset of
the columns, no key value is `None`, and no two rows share a key.

**Alternative considered:** a table name plus explicit column types. Rejected: the `Table` already exists for the
foreign key targets this change is for, and it supplies everything the probe table in D4 needs.

### D2: Row identity is the primary key, or an explicit `key`

Rows match by the values of the key columns. `key=None` means the `Table`'s primary key columns. A table with no primary
key needs an explicit `key`. A key change is a delete plus an insert, because identity cannot change.

The current side may hold duplicate keys when `key` is not unique in the database. The comparator raises `ValueError`
naming the table, because the diff has no correct answer.

### D3: Both sides compare PostgreSQL's `text` form of each value

`inspect_rows()` reads `SELECT col::text, ...` from the live table. `canonicalize_rows()` reads the same expression from
the probe table in D4. The diff compares the two strings. `NULL` reads as `None` on both sides.

Facts 1, 2, and 6 make this correct and convergent for every type with a text representation, which is every type. Fact
7 is the accepted cost: two values PostgreSQL calls equal can print differently, and the first run emits one `UPDATE`
that makes them print the same.

**Alternative considered:** a `FULL OUTER JOIN` with `ROW(...) IS DISTINCT FROM ROW(...)` in SQL. Rejected: `json` has
no equality operator (fact 2), so the query fails for a plausible metadata column.

**Alternative considered:** comparing driver-returned Python values. Rejected: the result depends on the driver, a
`dict` does not compare against `jsonb` key order, and every type would need its own rule.

### D4: The desired side round-trips through a typed `pg_temp` table with no defaults

`canonicalize_rows()` runs inside a savepoint:

1. Build a `Table` in schema `pg_temp` with one `Column(name, type)` per declared column, copied from the declared
   `Table`. No defaults, constraints, or indexes.
1. Execute `CreateTable` for it.
1. Execute `insert()` with the declared mappings. The typed columns adapt each Python value (fact 4).
1. Read `SELECT col::text, ...` in declaration order.
1. Roll back the savepoint, which drops the probe table (fact 3).

Types only, never defaults, because a copied `nextval()` default consumes the real sequence (fact 8). A native `sa.Enum`
column is declared as `Text` in the probe table. Its text form is the label, and the enum type may not exist in the
database yet.

The probe table comes from metadata, not from the catalog. A declared table that does not exist yet still canonicalizes,
so the `create_table` and the first `INSERT` statements share one migration.

### D5: The current side tolerates a missing table and missing columns

`inspect_table_columns()` returns the declared columns that exist in `pg_attribute`, or `None` when the table does not
exist. A missing table means no current rows. Every declared row is an insert.

A declared column the catalog lacks reads as `NULL` for every current row (fact 11). The only way to reach this state is
a column added in metadata and not yet migrated, because D1 rejects a column absent from the `Table`. The `add_column`
from Alembic and the `UPDATE` statements from this change then share one migration.

Columns that no declared row names are never read, compared, or written. An `INSERT` leaves them to their server
defaults.

### D6: One `op.execute()` per row, values quoted, identifiers through the preparer

```python
def upgrade() -> None:
    op.execute("DELETE FROM public.order_status WHERE code = 'draft'")
    op.execute("UPDATE public.order_status SET label = 'Paid in full' WHERE code = 'paid'")
    op.execute("INSERT INTO public.order_status (code, label, sort_order) VALUES ('shipped', 'Shipped', '30')")


def downgrade() -> None:
    op.execute("DELETE FROM public.order_status WHERE code = 'shipped'")
    op.execute("UPDATE public.order_status SET label = 'Paid' WHERE code = 'paid'")
    op.execute("INSERT INTO public.order_status (code, label, sort_order) VALUES ('draft', 'Draft', '5')")
```

Every value renders as a single-quoted literal with doubled single quotes (fact 9), and `NULL` renders bare. No literal
carries a cast, because the column context supplies the type (fact 5). An `UPDATE` sets only the columns whose text
changed and filters on the key columns. Schema, table, and column names go through
`autogen_context.dialect.identifier_preparer`, like `DropViewOp`.

One statement per row keeps each row on its own line. A diff of the migration file then shows one changed row as one
changed line.

**Alternative considered:** bare literals for numeric and boolean columns. Rejected for this change: it needs the column
type on the operation and a whitelist of safe text forms (fact 10). See the open questions.

**Alternative considered:** `op.bulk_insert()` with a `sa.table()` definition. Rejected: it renders Python literals,
which need `datetime`, `decimal`, and `uuid` imports in the migration file, and it has no update or delete form.

### D7: Operations carry full rows, so `reverse()` is exact

`InsertRowsOp`, `UpdateRowsOp`, and `DeleteRowsOp` each hold a `DataTableInfo` (schema, name, columns, key columns) and
the affected rows as tuples of `str | None` in column order. `DeleteRowsOp` carries the whole current row, so its
reverse is an `InsertRowsOp` that restores it. `UpdateRowsOp` carries `(current, desired)` pairs, and its reverse swaps
them. `InsertRowsOp` reverses to `DeleteRowsOp`.

`to_diff_tuple()` returns `("insert_rows", schema, name)` and the equivalents, matching the existing operations.

### D8: `expand_only` suppresses `DELETE` and nothing else

For an `expand_only` table, `diff_rows()` still reports the undeclared rows, and the comparator drops them from the
emitted operations. It logs each dropped key at `INFO`. Inserts and updates are emitted as usual.

This is the foreign key rule from the proposal (fact 12). To retire a value, keep its row declared and change a metadata
column such as `is_active`. The row stays, the foreign keys stay valid, and the update is in the migration.

The reverse of an `InsertRowsOp` is a `DeleteRowsOp` on every table, including expand-only tables. A downgrade restores
the exact prior state, and a `DELETE` blocked by a foreign key fails visibly at downgrade time. The generated file is
editable when a user prefers a no-op downgrade.

### D9: Row operations follow DDL operations, and tables follow foreign key order

The comparator registers as its own plugin, `alembic_pg_autogen.datatables`, at the `"schema"` level with
`DispatchPriority.MEDIUM`. Alembic's `PriorityDispatcher` runs same-priority comparators in registration order.
Alembic's own `alembic.autogenerate.tables` registers at import, and this package registers `compare`,
`checkconstraints`, and then `datatables` in `__init__.py`. Row operations therefore land after `create_table`,
`add_column`, and every function, trigger, and view operation. Alembic's downgrade reverses the list.

Across declared tables, `sqlalchemy.sql.ddl.sort_tables()` orders the `Table` objects by their foreign keys.
`DeleteRowsOp` instances come first in reverse dependency order. `UpdateRowsOp` and `InsertRowsOp` instances follow in
dependency order, updates before inserts within one table. A cycle falls back to declaration order with the warning
`sort_tables()` already emits.

### D10: `pg_data_tables` semantics differ from the DDL keys in one way

An absent key, `IGNORED`, and `[]` all manage nothing. There is no "drop undeclared tables" reading of an empty list,
because a row declaration names a table and cannot imply the absence of other tables. The key joins
`_DESIRED_STATE_KEYS` in `compare.py`, so `pg_data_table` triggers the misspelling warning.

A declared table is compared regardless of `include_schemas`, `include_object`, and `include_name`. The declaration is
explicit, and those filters govern what Alembic discovers, not what the user names.

The comparator returns `CONTINUE` without work when the connection is `None`, like the check constraint comparator.

### D11: Module layout follows the materialized view change

`inspect.py` gains `inspect_table_columns()` and `inspect_rows()`. `canonicalize.py` gains `canonicalize_rows()`.
`diff.py` gains `Row`, `RowDiff`, and `diff_rows()`. `ops.py` and `render.py` gain the three operations and renderers. A
new `compare_data_tables.py` holds `DataTable`, `DataTableInfo`, validation, and the comparator, mirroring
`compare_check_constraints.py`. `__init__.py` registers the plugin and exports the public names.

`diff_rows()` is a separate function from `diff()`. It keys on tuples of column values rather than on the identity
convention that `_diff_items()` uses, and `DiffResult` stays unchanged.

## Risks / Trade-offs

**[Text equality is stricter than PostgreSQL equality]** → `numeric` scale and `citext` case produce one `UPDATE` on the
first run (fact 7). Mitigation: the update is correct and idempotent, and the proposal documents it.

**[Hand-edited rows are reverted]** → A row changed in production by hand differs from the declaration, and the next
migration restores the declared value. Mitigation: this is the declarative contract, the same one DDL objects follow.
`expand_only` protects undeclared rows only, never edits to declared rows.

**[Unique constraints on non-key columns]** → Two rows that swap a unique `sort_order` fail at migration time under the
fixed update-then-insert order. Mitigation: the statements are visible and editable, and PostgreSQL names the
constraint.

**[Whole-table reads]** → A declared table with many rows makes autogenerate slow. Mitigation: none in this change. The
proposal names it as a non-goal.

**[A custom type that does not exist yet]** → `CreateTable` for the probe table fails when a non-enum custom type is
created in the same migration. Mitigation: the error names the type, and a second autogenerate run after the type exists
succeeds. Native enums are handled by the `Text` substitution in D4.

**[Session time zone]** → `timestamptz::text` prints in the session's `TimeZone`. Both sides read in the same session,
so the comparison is stable. The rendered literal carries an offset, so it applies correctly under any time zone.

## Migration Plan

Not applicable. The feature is opt-in and inert until `pg_data_tables` names a table.

## Open Questions

1. **Bare numeric and boolean literals.** D6 quotes every value, so `sort_order` renders as `'30'`. Rendering `Integer`,
   `Numeric`, `Float`, and `Boolean` columns bare needs the column type on `DataTableInfo` and a whitelist of safe text
   forms (fact 10). Is the readability worth the extra state?
1. **Log level for undeclared rows.** D8 logs each kept row at `INFO`. `WARNING` is more visible and repeats on every
   run.
1. **A policy enum instead of a flag.** `expand_only: bool` covers the two known cases. A `RowPolicy` enum with `SYNC`
   and `EXPAND` leaves room for a `STRICT` mode later, at the cost of a less direct declaration today.
1. **Downgrade of an insert on an expand-only table.** D8 emits a `DELETE`. A no-op downgrade would honor the "never
   delete" intent and would make the migration irreversible.
