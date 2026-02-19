## Why

The catalog inspector and canonicalization layers can now read current database state and normalize desired state
through PostgreSQL. There is no logic to compare these two canonical representations and produce a list of operations
(create, replace, drop). Without a diff layer, Alembic autogenerate has no way to know what changed.

## What Changes

- New `diff` function that compares canonical current state against canonical desired state, producing a sequence of
  typed diff operations (create, replace, drop) for functions and triggers.
- Identity-based matching: functions matched by `(schema, name, identity_args)`, triggers by
  `(schema, table_name, trigger_name)`.
- Definition comparison using the canonical DDL strings from `pg_get_functiondef()` / `pg_get_triggerdef()` — objects
  with matching identity but differing definitions produce replace operations.
- Drop detection: objects present in the database but absent from the desired state produce drop operations.
- Create detection: objects present in the desired state but absent from the database produce create operations.
- Operation types carry the full `FunctionInfo` / `TriggerInfo` so downstream layers (Alembic ops, renderers) have
  everything they need to emit DDL.

## Capabilities

### New Capabilities

- `diff`: Compare two canonical catalog snapshots (current vs. desired) and produce a typed sequence of diff operations
  (create, replace, drop) for functions and triggers.

### Modified Capabilities

(none)

## Impact

- New module `_diff.py` in `src/alembic_pg_autogen/`.
- New public types exported from `__init__.py` (diff operation types and the diff function).
- Depends on `FunctionInfo` and `TriggerInfo` from `_inspect.py` — no changes to those types.
- No new runtime dependencies beyond what's already declared.
- Unit-testable without a database (operates on in-memory data structures), plus integration tests that exercise the
  full pipeline (inspect → canonicalize → diff).
