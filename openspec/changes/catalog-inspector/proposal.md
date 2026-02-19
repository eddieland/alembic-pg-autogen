## Why

Alembic autogenerate needs to know what functions and triggers currently exist in the database before it can diff them
against desired state. The catalog inspection layer is the foundation — everything else (canonicalization, diffing,
migration generation) depends on being able to bulk-load object definitions from PostgreSQL's system catalog
efficiently. Existing tools like alembic_utils perform 5-9 roundtrips per entity; querying the catalog directly reduces
this to O(1) bulk queries regardless of entity count.

## What Changes

- Add a catalog inspection module that queries `pg_proc` and `pg_trigger` to bulk-load function and trigger definitions
  from PostgreSQL (>= 14).
- Use `pg_get_functiondef()` and `pg_get_triggerdef()` to retrieve canonical DDL representations.
- Return structured Python representations (not raw SQL strings) with identity fields separated from definitions.
- Filter to user schemas only (exclude `pg_catalog`, `information_schema`) and to regular functions/procedures only
  (exclude aggregates and window functions).
- Accept a SQLAlchemy `Connection` as input — no connection management of its own.

## Non-goals

- Desired-state declaration or canonicalization (future change).
- Diffing or comparison logic (future change).
- Alembic integration hooks (future change).
- Support for views, policies, or other object types beyond functions and triggers.
- Support for PostgreSQL versions below 14.

## Capabilities

### New Capabilities

- `catalog-inspector`: Bulk-load function and trigger definitions from PostgreSQL system catalogs, returning structured
  representations with identity and definition fields.

### Modified Capabilities

(none)

## Impact

- New module `alembic_pg_autogen._inspect` (or similar) in `src/alembic_pg_autogen/`.
- New integration tests requiring the PostgreSQL test container fixture from `library-foundation`.
- No changes to existing modules or public API — purely additive.
- Runtime dependency on SQLAlchemy's `text()` for catalog queries (already a dependency).
