## Why

The catalog-inspector spec gave us the ability to bulk-load the *current* database state as canonical
`FunctionInfo`/`TriggerInfo` instances. To diff current vs. desired state, we need the desired side in the same
canonical format. Users declare functions and triggers as SQL strings (often generated programmatically), but raw user
SQL won't match PostgreSQL's canonical output — differences in whitespace, casing, implicit defaults, and identifier
qualification produce false positives. The solution from our research is to let PostgreSQL itself canonicalize the
desired state: CREATE desired objects in a transaction, read them back via `pg_get_*`, then ROLLBACK.

## What Changes

- New `_canonicalize` module with `canonicalize_functions()` and `canonicalize_triggers()` public functions.
- These accept sequences of user-declared DDL strings, execute them inside a transaction against a live PostgreSQL
  connection, query the catalog to read back canonical forms, then rollback — leaving the database unchanged.
- Return the same `FunctionInfo` and `TriggerInfo` types from catalog-inspector, making downstream diffing a
  straightforward sequence comparison.
- Batch approach: all desired objects are created in a single transaction and read back in a single catalog query,
  yielding ~4 roundtrips total regardless of entity count.

## Non-goals

- **No SQL parsing** — the library never parses or templates SQL strings. PostgreSQL is the canonicalizer.
- **No user-facing declaration types** — users pass raw DDL strings (`CREATE FUNCTION ...`), not decomposed dataclass
  instances. A higher-level declaration API may come later but is out of scope here.
- **No diffing** — this spec only canonicalizes desired state. Comparing canonical desired vs. canonical actual is a
  separate spec.
- **No PL/pgSQL body normalization** — `pg_get_functiondef()` preserves PL/pgSQL bodies verbatim. Whitespace
  normalization of function bodies is deferred to the diff layer.
- **No offline mode** — canonicalization requires a live PostgreSQL connection by design.

## Capabilities

### New Capabilities

- `desired-state-canonicalization`: Accept user-provided DDL strings for functions and triggers, round-trip them through
  PostgreSQL's catalog to produce canonical `FunctionInfo`/`TriggerInfo` instances, and roll back without modifying the
  database.

### Modified Capabilities

(none)

## Impact

- **New module**: `src/alembic_pg_autogen/_canonicalize.py`
- **Public API additions**: `canonicalize_functions()`, `canonicalize_triggers()` exported from `__init__.py`
- **Dependencies**: No new runtime dependencies (uses existing SQLAlchemy + `pg_get_*` catalog functions)
- **Test infrastructure**: Integration tests using the existing `pg_engine` fixture with ephemeral PostgreSQL
- **Existing code**: No modifications to `_inspect.py` — reuses its `FunctionInfo` and `TriggerInfo` types as-is
