## Context

The catalog inspector (`_inspect.py`) queries `pg_proc` and `pg_trigger` to discover all user-defined functions and
triggers in managed schemas. Today, the comparator's `_filter_to_declared()` function masks extension-owned objects by
only diffing objects the user explicitly declared. Once `drop-undeclared-objects` removes that filter, every
extension-installed function (PostGIS alone adds 500+) in a managed schema will generate a spurious `DROP` operation.

PostgreSQL already tracks extension membership: `pg_depend` records a dependency row with `deptype = 'e'` (extension
member) linking each extension-owned object back to its owning extension in `pg_extension`. We can use this at query
time to exclude extension-owned objects before they ever reach the diff layer.

## Goals / Non-Goals

**Goals:**

- Extension-owned functions and triggers are silently excluded from `inspect_functions` and `inspect_triggers` results.
- The filtering happens in SQL (single query, no extra round-trips).
- An end-to-end test proves PostGIS functions are excluded while user functions in the same schema are retained.

**Non-Goals:**

- Providing a user opt-in to *include* extension objects (can be added later if needed).
- Filtering extension-owned views (will be addressed when views support lands).
- Filtering by extension name (e.g., exclude PostGIS but include pgcrypto) — all extensions are excluded uniformly.

## Decisions

### 1. Filter in SQL via `NOT EXISTS` subquery

Add a `NOT EXISTS (SELECT 1 FROM pg_depend ...)` clause to both `_FUNCTIONS_QUERY` and `_TRIGGERS_QUERY`.

**For functions** (`pg_proc`):

```sql
AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_depend d
    WHERE d.classid = 'pg_catalog.pg_proc'::regclass
      AND d.objid = p.oid
      AND d.deptype = 'e'
)
```

**For triggers** (`pg_trigger`):

```sql
AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_depend d
    WHERE d.classid = 'pg_catalog.pg_trigger'::regclass
      AND d.objid = t.oid
      AND d.deptype = 'e'
)
```

**Why `NOT EXISTS` over `LEFT JOIN ... IS NULL`**: `NOT EXISTS` short-circuits on the first match and produces an
identical query plan to an anti-join in modern PostgreSQL. It's also more readable for a negative filter.

**Why in SQL rather than Python post-filter**: Extension objects should never enter the pipeline. Filtering in Python
would require fetching hundreds of rows only to discard them, and would introduce a dependency on `pg_depend` data being
available in the Python layer.

**Alternative considered — `pg_extension` + `pg_depend` JOIN**: A join through `pg_extension` would let us filter by
extension name, but that's out of scope. The `deptype = 'e'` check alone is sufficient and simpler.

### 2. End-to-end test with PostGIS Docker image

Use `postgis/postgis:{pg_version}-3.5` as the Docker image (via testcontainers) to get a PostgreSQL instance with
PostGIS pre-installed.

**Test fixture strategy**: Add a separate session-scoped `postgis_engine` fixture in the e2e test file (or a dedicated
conftest) that uses the PostGIS image. This keeps the existing `pg_engine` fixture unchanged — most tests don't need
PostGIS and shouldn't pay the heavier image pull cost.

**Test structure**:

1. `CREATE EXTENSION postgis` in the test schema to install extension functions.
1. Create a user-defined function in the same schema.
1. Call `inspect_functions(conn, [schema])`.
1. Assert: user function is returned, no PostGIS `ST_*` functions are returned.
1. Verify a specific well-known PostGIS function (e.g., `ST_Area`) exists via direct `pg_proc` query but is absent from
   results.

**Pytest marker**: Use `@pytest.mark.postgis` so these tests can be skipped when the PostGIS image is unavailable or in
fast CI runs.

### 3. No signature changes

`inspect_functions` and `inspect_triggers` keep their existing signatures. The extension exclusion is unconditional —
there is no `exclude_extensions=True` parameter. Extension-owned objects are implementation details of the database, not
user-managed state. If a future use case requires inspecting extension objects, a separate function or flag can be added
then.

## Risks / Trade-offs

**Performance of `NOT EXISTS` subquery**: `pg_depend` is indexed on `(classid, objid, objsubid)`, so the correlated
subquery resolves via index lookup per row. On a database with 500 user functions, this adds ~500 index probes — well
under 1ms total. No risk.

**Extension functions the user intentionally wraps**: If a user `CREATE OR REPLACE`s an extension function (overriding
it), the function remains extension-owned in `pg_depend`. It will be excluded from inspection. This is an acceptable
edge case — users who override extension functions should manage them outside this tool. → Mitigation: document this
behavior.

**PostGIS image size in CI**: The `postgis/postgis` image is ~500MB vs ~200MB for `postgres`. This increases CI cache
and pull time. → Mitigation: Use a separate pytest marker so PostGIS tests can be run in a dedicated CI job or skipped
in fast-feedback loops.

**Trigger extension membership**: Most extensions don't install triggers (PostGIS doesn't), but some might (e.g.,
`pg_partman`). The `pg_trigger` exclusion handles this correctly. Low risk, but the e2e test focuses on functions since
that's the primary pain point.
