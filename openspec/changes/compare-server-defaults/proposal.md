## Why

Alembic's `compare_server_default` is opt-in, and on PostgreSQL it decides equality by asking the server to compare the
two defaults **as values**: `SELECT <catalog_default> = <metadata_default>`. Alembic's own source flags the problem in a
comment there — "this seems quite a bad idea for a default that's a SQL function! SQL functions are not deterministic!"

Value equality is the wrong relation for a DDL default. Verified against PostgreSQL 16.13 with Alembic 1.19.1:

- **It crashes autogenerate.** A `json`, `xml`, `point`, or `polygon` default has no `=` operator, so the comparison
  raises `UndefinedFunction` *and aborts the enclosing transaction* — the whole run dies, not just that one column.
- **It mutates the database.** A `nextval('s')` default is executed to compare it; one sequence advanced 1 → 2 → 4
  across two `--autogenerate` runs.
- **It never converges.** `server_default=text("NULL")` emits a spurious `alter_column` on every run forever: PostgreSQL
  discards a literal `NULL` default, so applying the migration never satisfies it.

This library already owns the right primitive: comparing the *deparsed expression*, as
`canonicalize_check_constraints()` does. `ALTER COLUMN ... SET DEFAULT` stores a parse tree without evaluating it, so
the same savepoint round-trip fixes all three — `'{}'` normalizes to `'{}'::json` with no crash, `nextval('s')` to
`nextval('s'::regclass)` with no sequence consumed, `NULL` to the absent default the catalog holds.

## What Changes

- Add `ServerDefaultInfo` and `inspect_server_defaults()` to the catalog inspector, reading
  `pg_get_expr(adbin, adrelid)` from `pg_attrdef` joined to `pg_attribute`
- Add `canonicalize_server_defaults()`, normalizing desired defaults by applying them with
  `ALTER COLUMN ... SET DEFAULT` inside a rolled-back savepoint and reading them back through the same deparse
- Add a column-level comparator that compares the two canonical forms and emits Alembic's own `AlterColumnOp`
- Register it as its own plugin, `alembic_pg_autogen.serverdefaults`, disableable independently

**Key difference from check constraints**: there the two comparators were disjoint by construction; here they overlap,
both deciding the same column. Alembic's runs only under `compare_server_default=True`, off by default. Whether to warn,
defer, or suppress is a design question.

## Non-goals

- **Semantic equivalence beyond deparsing** — `now()` and `CURRENT_TIMESTAMP` parse to different trees and register as a
  change where value comparison called them equal, as do `0` and `0.0`. A spurious no-op migration is the accepted cost
  of never executing a default
- **Client-side defaults** (`default=`) — evaluated in Python, never present in the catalog
- **`SERIAL` and identity columns** — the default is sequence machinery owned by the column's type
- **Generated columns** (`GENERATED ALWAYS AS`) — different catalog shape (`attgenerated`), different lifecycle
- **A `pg_server_defaults` DDL-string channel** — defaults belong to columns Alembic already manages, as with check
  constraints

## Capabilities

### New Capabilities

- `server-default-comparison`: expression-level comparison of metadata server defaults against the live catalog

### Modified Capabilities

- `catalog-inspector`: add `ServerDefaultInfo` and `inspect_server_defaults()`
- `canonicalization`: add `canonicalize_server_defaults()`

## Impact

- **Config**: none — activates on its own for PostgreSQL
- **Behavior**: server defaults are compared where Alembic compares nothing by default, and the three failure modes
  above go away for users who had opted in
- **Dependencies**: none new
- **Public API**: new exports — `ServerDefaultInfo`, `inspect_server_defaults`, `canonicalize_server_defaults`
