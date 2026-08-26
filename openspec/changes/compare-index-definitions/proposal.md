## Why

Alembic's index signature covers columns, expressions, uniqueness, and `NULLS NOT DISTINCT`. It does not cover
`postgresql_where`, `postgresql_using`, `postgresql_include`, or `postgresql_ops` — those four are absent from the
comparison entirely. Change one in a model and autogenerate emits nothing. Not a wrong migration: no migration, on every
run, forever.

Verified against PostgreSQL 16.13 with Alembic 1.19.1 and SQLAlchemy 2.0.46, each case a silent miss:

- **Partial indexes.** Adding, removing, or changing a `WHERE` predicate produces no diff. A `deleted_at IS NULL` index
  and an unfiltered one compare as the same index.
- **Access method.** `btree` in the catalog against `postgresql_using="gin"` in the model: no diff.
- **Operator classes.** A default opclass against `text_pattern_ops`, or GIN's `jsonb_ops` against `jsonb_path_ops`: no
  diff. Alembic also warns and gives up outright when an opclass appears inside an expression.
- **`INCLUDE`.** Adding covering columns produces no diff.

The expressions Alembic *does* compare go through `_cleanup_index_expr`, a regex that lowercases the text and strips
quotes, casts, and spaces before comparing. It is lossy in the unsafe direction: an index on `a` and one on `a::int`
reduce to the same string and compare equal.

This library already owns the right primitive. `pg_get_indexdef()` is the `pg_get_expr()` of indexes — one canonical
string covering expressions, opclasses, access method, `INCLUDE`, `NULLS NOT DISTINCT`, storage parameters, and the
predicate — and the savepoint round-trip that `canonicalize_check_constraints()` uses works the same way here.

## What Changes

- Add `IndexInfo` and `inspect_indexes()` to the catalog inspector, reading `pg_get_indexdef()` and stripping the
  statement's identity prefix in SQL so what remains compares as a plain string
- Add `canonicalize_indexes()`, normalizing desired indexes by creating them on an empty `TEMP` clone of the target
  table inside a rolled-back savepoint
- Add a table-level comparator that compares the two canonical forms and emits Alembic's own `DropIndexOp` /
  `CreateIndexOp` pair
- Register it as its own plugin, `alembic_pg_autogen.indexes`, disableable independently
- Add `CreateIndexConcurrentlyOp` / `DropIndexConcurrentlyOp` and their renderers, opt-in via `pg_index_concurrently`,
  wrapping Alembic's rendered call in `op.get_context().autocommit_block()`

**Key difference from check constraints**: there the probe was free, because `NOT VALID` skips validation.
`CREATE INDEX` really builds the index — measured at 1.2s for an expression index and 2.1s for a GIN index on a 500k-row
table, against 0.8ms on an empty clone. So the probe moves off the real table, which also keeps autogenerate from
holding a lock on it.

**Key difference from Alembic's comparator**: the two overlap rather than being disjoint, because Alembic does compare
some of what an index is. This comparator runs at `DispatchPriority.LAST` and skips any index Alembic already emitted
operations for, so one index never draws two drop/create pairs.

## Non-goals

- **Indexes outside `target_metadata`** — no `pg_indexes` DDL-string channel, for the reason check constraints have
  none: it would put this library in a fight with Alembic over which of them owns an index
- **Unnamed indexes** — they cannot be matched between metadata and catalog by name
- **Constraint-backed indexes** — a primary key, unique, or exclusion constraint owns its index, and Alembic compares
  those as constraints
- **Index existence** — adding and removing an index stays Alembic's, as does any index it has already decided differs
- **Tablespaces and collations** — `pg_get_indexdef()` omits the tablespace, and a collation change is a column change
  rather than an index one
- **Running `CREATE INDEX CONCURRENTLY` during autogenerate** — the opt-in changes what is *rendered*; the probe is
  always an ordinary index on a throwaway clone

## Capabilities

### New Capabilities

- `index-comparison`: definition-level comparison of metadata indexes against the live catalog

### Modified Capabilities

- `catalog-inspector`: add `IndexInfo` and `inspect_indexes()`
- `canonicalization`: add `canonicalize_indexes()`
- `alembic-operations`: add `CreateIndexConcurrentlyOp` and `DropIndexConcurrentlyOp`
- `alembic-render`: add renderers wrapping an index operation in an autocommit block

## Impact

- **Config**: none to get the comparison; `pg_index_concurrently=True` opts into concurrent rendering
- **Behavior**: a changed predicate, access method, operator class, or `INCLUDE` list now produces a migration where it
  previously produced silence
- **Dependencies**: none new
- **Public API**: new exports — `IndexInfo`, `inspect_indexes`, `canonicalize_indexes`, `CreateIndexConcurrentlyOp`,
  `DropIndexConcurrentlyOp`
