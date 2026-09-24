## Why

The Alembic index signature contains columns, expressions, uniqueness, and `NULLS NOT DISTINCT`. It does not contain
`postgresql_where`, `postgresql_using`, `postgresql_include`, or `postgresql_ops`. Alembic never compares those four
options. If you change one of them in a model, autogenerate emits no migration. It emits no migration on later runs too.

We tested each case below against PostgreSQL 16.13, Alembic 1.19.1, and SQLAlchemy 2.0.46. Each case gives no operation:

- **Partial indexes.** A new, removed, or changed `WHERE` predicate gives no difference. A `deleted_at IS NULL` index
  and an index with no filter compare as equal.
- **Access method.** `btree` in the catalog and `postgresql_using="gin"` in the model give no difference.
- **Operator classes.** A default operator class and `text_pattern_ops` give no difference. GIN `jsonb_ops` and
  `jsonb_path_ops` give no difference. When an operator class is inside an expression, Alembic logs a warning and does
  not compare the index.
- **`INCLUDE`.** New covering columns give no difference.

Alembic compares expressions through `_cleanup_index_expr`. This regular expression lowercases the text. It also removes
quotes, casts, and spaces before the comparison. The function loses information, and the loss hides differences. An
index on `a` and an index on `a::int` give the same string, so they compare as equal.

This library already has the necessary tool. `pg_get_indexdef()` does for indexes what `pg_get_expr()` does for
expressions. It returns one canonical string. The string contains expressions, operator classes, the access method,
`INCLUDE`, `NULLS NOT DISTINCT`, storage parameters, and the predicate. The savepoint round trip of
`canonicalize_check_constraints()` also works for indexes.

## What Changes

- Add `IndexInfo` and `inspect_indexes()` to the catalog inspector. The query reads `pg_get_indexdef()` and removes the
  identity prefix of the statement in SQL. The remaining text compares as a plain string.
- Add `canonicalize_indexes()`. It normalizes each desired index. It creates the index on an empty `TEMP` copy of the
  target table inside a savepoint, and then rolls the savepoint back.
- Add a table comparator. It compares the two canonical forms and emits the Alembic `DropIndexOp` and `CreateIndexOp`.
- Register the comparator as a separate plugin, `alembic_pg_autogen.indexes`. Users can disable it alone.
- Add `CreateIndexConcurrentlyOp`, `DropIndexConcurrentlyOp`, and their renderers. The option `pg_index_concurrently`
  enables them. The renderers put the Alembic call inside `op.get_context().autocommit_block()`.

**Difference from check constraints:** For check constraints, the test cost nothing, because `NOT VALID` skips
validation. `CREATE INDEX` builds the index. On a table with 500,000 rows, an expression index took 1.2 s and a GIN
index took 2.1 s. On an empty copy, the same test took 0.8 ms. Thus the test runs on a copy, not on the real table. The
copy also prevents a lock on the real table during autogenerate.

**Difference from the Alembic comparator:** The two comparators can examine the same index, because Alembic compares
some parts of an index. This comparator runs at `DispatchPriority.LAST`. It skips each index that already has an Alembic
operation. Thus one index never gets two drop/create pairs.

## Non-goals

- **Indexes outside `target_metadata`:** There is no `pg_indexes` channel for DDL strings. Check constraints have no
  such channel for the same reason. The channel makes this library and Alembic both claim ownership of an index.
- **Unnamed indexes:** The comparator cannot match them between metadata and catalog by name.
- **Indexes that implement constraints:** A primary key, unique, or exclusion constraint owns its index. Alembic
  compares those objects as constraints.
- **Index existence:** Alembic continues to add and remove indexes. Alembic also keeps each index that it already found
  different.
- **Tablespaces and collations:** `pg_get_indexdef()` does not write the tablespace. A collation change is a change to a
  column, not to an index.
- **`CREATE INDEX CONCURRENTLY` during autogenerate:** The option changes the *rendered* migration only. The test always
  creates an ordinary index on an empty copy.

## Capabilities

### New Capabilities

- `index-comparison`: compares the definitions of metadata indexes with the live catalog

### Modified Capabilities

- `catalog-inspector`: add `IndexInfo` and `inspect_indexes()`
- `canonicalization`: add `canonicalize_indexes()`
- `alembic-operations`: add `CreateIndexConcurrentlyOp` and `DropIndexConcurrentlyOp`
- `alembic-render`: add renderers that put an index operation inside an autocommit block

## Impact

- **Config**: The comparison needs no configuration. `pg_index_concurrently=True` enables concurrent rendering.
- **Behavior**: A changed predicate, access method, operator class, or `INCLUDE` list now gives a migration. Before this
  change, it gave no operation.
- **Dependencies**: none new
- **Public API**: The new exports are `IndexInfo`, `inspect_indexes`, `canonicalize_indexes`,
  `CreateIndexConcurrentlyOp`, and `DropIndexConcurrentlyOp`.
