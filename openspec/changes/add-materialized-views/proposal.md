## Why

The `add-views` change deferred materialized views because their lifecycle differs from regular views. Users who manage
reporting or caching layers need autogenerate support for materialized views. Prototype work against PostgreSQL 16
(2026-08-27) confirmed that the existing catalog-first canonicalization pattern extends to materialized views with three
adjustments. The adjustments are: drop-first canonicalization, `DROP` + `CREATE` replacement, and index preservation.

## What Changes

- Add `MaterializedViewInfo` NamedTuple and `inspect_materialized_views()` catalog inspector (`relkind = 'm'`)
- Add `inspect_matview_indexes()` to read index DDL for one materialized view via `pg_get_indexdef()`
- Add `matview_ddl` parameter to `canonicalize()` and a `canonicalize_materialized_views()` wrapper
- Extend `CanonicalState` with a `materialized_views` field
- Add `MaterializedViewOp` diff type and a `materialized_view_ops` field to `DiffResult`
- Add `CreateMaterializedViewOp`, `ReplaceMaterializedViewOp`, `DropMaterializedViewOp` Alembic operations
- Add renderers for the three operations
- Add a `pg_materialized_views` desired-state configuration key
- Extend the comparator pipeline and the dependency-safe operation ordering (6 groups become 8)

**Key lifecycle differences, proven by prototype:**

- PostgreSQL has no `CREATE OR REPLACE MATERIALIZED VIEW` and no `ALTER MATERIALIZED VIEW ... AS`. A definition change
  requires `DROP` + `CREATE`.
- `CREATE MATERIALIZED VIEW IF NOT EXISTS` keeps the old definition silently. Canonicalization must drop the existing
  object first inside the savepoint.
- `postgast.ensure_or_replace()` passes materialized view DDL through unchanged. `postgast.to_drop()` raises
  `ValueError` for it. The render layer must build `DROP MATERIALIZED VIEW` from catalog identifiers.
- Canonicalization forces `WITH NO DATA` through `postgast.deparse()`. This skips query execution during autogenerate.
  `pg_get_viewdef()` output is identical for populated and unpopulated materialized views.
- `DROP MATERIALIZED VIEW` drops the indexes on the materialized view silently. `REFRESH ... CONCURRENTLY` requires a
  unique index. Replacement therefore re-creates the current indexes from `pg_get_indexdef()` output.

## Non-goals

- **Index diffing**. Indexes are preserved on replacement, not compared. Declaring desired indexes is a later change.
- **Refresh scheduling**. `REFRESH MATERIALIZED VIEW` timing and orchestration stay with the user.
- **Population state**. `relispopulated` and the `WITH [NO] DATA` clause do not participate in comparison. Rendered
  `CREATE` statements populate by default.
- **Dependency graphs**. A regular view that reads a materialized view is unsupported, because views execute before
  materialized views. PostgreSQL reports the error. Users order DDL inside each list themselves.
- **Storage options**. Tablespaces, access methods, and storage parameters do not round-trip through `pg_get_viewdef()`
  and are not compared.

## Capabilities

### New Capabilities

_(none. Materialized views thread through all existing layers.)_

### Modified Capabilities

- `catalog-inspector`: Add `MaterializedViewInfo`, `inspect_materialized_views()`, and `inspect_matview_indexes()`
- `canonicalization`: Add `matview_ddl` with drop-first, `WITH NO DATA` execution; extend `CanonicalState`
- `diff`: Add `MaterializedViewOp` and `materialized_view_ops`
- `alembic-operations`: Add the three materialized view operations; `ReplaceMaterializedViewOp` carries index DDL
- `alembic-compare`: Add `pg_materialized_views` key, pipeline wiring, and 8-group operation ordering
- `alembic-render`: Add renderers; replacement renders `DROP`, `CREATE`, and index re-creation statements

## Impact

- **Types**: `CanonicalState` and `DiffResult` gain appended fields. **BREAKING** for positional destructuring.
- **Config**: New `pg_materialized_views` key in `context.configure()` opts. Existing configurations are unaffected.
- **Public API**: New exports mirror the view exports.
- **Tests**: Unit tests per layer plus e2e tests covering replacement, index preservation, and rollback safety.
