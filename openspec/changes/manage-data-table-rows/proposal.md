## Why

Many schemas replace a native `ENUM` type with a lookup table. An `orders.status` column holds a text foreign key into
`order_status (code, label, sort_order)`. A new value is a row, not a type change. Alembic manages the table's DDL but
knows nothing about its rows. Today a developer writes the `INSERT` and the matching `DELETE` by hand for every new
value.

Nothing in those statements is a decision. The rows are declared in code, usually next to a Python `Enum`. This is the
same shape as functions and triggers: declared state, inspected state, and a rendered diff.

A row that a foreign key references cannot be deleted. Some tables therefore only ever grow. Removing a value from code
must not produce a `DELETE` for those tables.

## What Changes

- Add a `DataTable` declaration: a SQLAlchemy `Table`, the desired rows as mappings, an optional key, and an
  `expand_only` flag
- Add a `pg_data_tables` configuration key that accepts a sequence of `DataTable` declarations
- Add `inspect_table_columns()` and `inspect_rows()` to the catalog inspector, reading each value as `text`
- Add `canonicalize_rows()`, which inserts the declared rows into a typed `pg_temp` table inside a savepoint and reads
  them back through the same `text` cast
- Add `diff_rows()`, which matches rows by key and classifies each as insert, update, delete, or unchanged
- Add `InsertRowsOp`, `UpdateRowsOp`, and `DeleteRowsOp`, with renderers that emit one `op.execute()` per row
- Add a `"schema"` level comparator registered as its own plugin, `alembic_pg_autogen.datatables`
- Emit no `DELETE` for a table declared `expand_only=True`. Undeclared rows stay and are logged.

## Non-goals

- **Tables without a `Table` object**. The `Table` supplies the schema, the primary key, and the column types. A user
  without a model constructs one by hand.
- **SQL expressions as row values**. Values are Python values. A column that needs `now()` stays undeclared and takes
  its server default on `INSERT`.
- **Partial rows**. Every declared row supplies the same columns. Undeclared columns are never compared or written.
- **Large tables**. The comparator reads every row of each declared table.
- **Semantic equality**. Values are equal when PostgreSQL prints them identically. `1.5` and `1.50` in a `numeric`
  column differ once, and the `UPDATE` makes the next run clean.
- **A strict mode** that fails autogenerate when an expand-only table holds an undeclared row.

## Capabilities

### New Capabilities

- `data-table-rows`: declaration, comparison, and migration of rows in lookup tables

### Modified Capabilities

- `catalog-inspector`: add `inspect_table_columns()` and `inspect_rows()`
- `canonicalization`: add `canonicalize_rows()`
- `diff`: add `Row`, `RowDiff`, and `diff_rows()`
- `alembic-operations`: add the three row operations
- `alembic-render`: add the three renderers
- `alembic-compare`: add `pg_data_tables` to the keys the misspelling warning recognizes

## Impact

- **Config**: new `pg_data_tables` key. Absent, `IGNORED`, and `[]` all manage nothing.
- **Behavior**: none for existing users. The plugin emits nothing until a table is declared.
- **Types**: `CanonicalState` and `DiffResult` are unchanged.
- **Public API**: `DataTable`, `DataTableInfo`, `Row`, `RowDiff`, the three operations, and the four layer functions.
