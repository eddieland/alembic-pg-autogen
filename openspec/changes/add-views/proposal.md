## Why

Views are the most commonly managed PostgreSQL object type after functions and triggers. Adding view support is the
natural next step for the library and — because `pg_get_viewdef()` exists — views fit cleanly into the existing
catalog-first canonicalization pattern. Implementing views now will also validate and refine the generic patterns
(`_diff_items`, operation/renderer boilerplate) before tackling harder object types.

## What Changes

- Add `ViewInfo` NamedTuple and `inspect_views()` catalog inspector
- Add `view_ddl` parameter to `canonicalize()` and `canonicalize_views()` convenience wrapper
- Extend `CanonicalState` with a `views` field
- Add `ViewOp` diff type and `view_ops` field to `DiffResult`
- Add `CreateViewOp`, `ReplaceViewOp`, `DropViewOp` Alembic operations
- Add renderers for view operations
- Add `pg_views` desired-state configuration key
- Update comparator pipeline and dependency-safe operation ordering to include views

**Key pattern difference**: Unlike `pg_get_functiondef()` which returns complete DDL, `pg_get_viewdef()` returns only
the query body. The inspection/canonicalization layers must reconstruct the full `CREATE VIEW schema.name AS <query>`
DDL. This establishes a pattern for future object types where canonical DDL must be assembled from catalog fields rather
than read from a single `pg_get_*()` call.

## Non-goals

- **Materialized views** — different lifecycle (no `CREATE OR REPLACE`, requires `REFRESH`), will be a separate change
- **View dependency ordering** — views can depend on other views, but we won't build a dependency graph; PostgreSQL will
  error on invalid ordering and users can reorder DDL declarations
- **Column aliases and view options** — `WITH CHECK OPTION`, `SECURITY BARRIER`, column aliases are supported only to
  the extent that PostgreSQL round-trips them through `pg_get_viewdef()`
- **Recursive view detection** — no special handling for recursive views beyond what PostgreSQL provides

## Capabilities

### New Capabilities

_(none — views are threaded through all existing layers)_

### Modified Capabilities

- `catalog-inspector`: Add `ViewInfo` type and `inspect_views()` function using `pg_class` + `pg_get_viewdef()`
- `canonicalization`: Add `view_ddl` parameter to `canonicalize()`, `views` field to `CanonicalState`, and
  `canonicalize_views()` wrapper
- `diff`: Add `ViewOp` type and `view_ops` field to `DiffResult`
- `alembic-operations`: Add `CreateViewOp`, `ReplaceViewOp`, `DropViewOp` extending `MigrateOperation`
- `alembic-compare`: Add `pg_views` config key, include views in pipeline, update operation ordering (views depend on
  functions, triggers may depend on views)
- `alembic-render`: Add renderers for the three view operation types

## Impact

- **Types**: `CanonicalState` and `DiffResult` gain new fields — **BREAKING** for any code destructuring these tuples
  positionally (unlikely given the project's age, but technically a public API change)
- **Config**: New `pg_views` key in `context.configure()` opts
- **Operation ordering**: Grows from 4 groups to 6 (drop triggers → drop views → drop functions → create/replace
  functions → create/replace views → create/replace triggers)
- **Public API**: New exports — `ViewInfo`, `ViewOp`, `inspect_views`, `canonicalize_views`, `CreateViewOp`,
  `ReplaceViewOp`, `DropViewOp`
- **Tests**: New unit tests per layer + e2e integration tests with views
