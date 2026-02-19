## Why

When `drop-undeclared-objects` behavior is active, the comparator diffs *all* objects in managed schemas against the
declared desired state. Extensions like PostGIS, pgcrypto, and pg_trgm install dozens to hundreds of functions into
`public` — every one of which would generate a spurious `DROP` operation. Users shouldn't have to manually exclude
extension-owned objects; PostgreSQL already tracks ownership via `pg_depend`.

## What Changes

- Add a `NOT EXISTS` subquery against `pg_depend` (with `deptype = 'e'`) to both the functions and triggers inspection
  queries, filtering out objects owned by any extension.
- Add an end-to-end test using a PostGIS PostgreSQL Docker image that installs the extension, creates user functions
  alongside it, and verifies only user functions appear in inspection results (extension functions are excluded).

## Capabilities

### New Capabilities

- `e2e-postgis`: End-to-end test infrastructure using a PostGIS Docker image to verify extension-object exclusion.

### Modified Capabilities

- `catalog-inspector`: The inspection queries gain a `pg_depend` exclusion clause so extension-owned functions and
  triggers are never returned.

## Impact

- **`_inspect.py`**: Both `_FUNCTIONS_QUERY` and `_TRIGGERS_QUERY` gain a `NOT EXISTS (... pg_depend ... deptype='e')`
  subquery.
- **Tests**: New end-to-end test file requiring a PostGIS-enabled PostgreSQL instance (Docker). CI may need a
  `postgis/postgis` service image.
- **No API changes**: `inspect_functions` and `inspect_triggers` signatures are unchanged. Callers see fewer results
  (extension objects silently excluded).
- **No breaking changes**: This is purely additive filtering — existing users who don't use extensions see no
  difference.
