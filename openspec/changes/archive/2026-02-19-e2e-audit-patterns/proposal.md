## Why

The core pipeline (inspect → canonicalize → diff → ops → render) is complete, but every existing integration test uses
trivial single-function or single-trigger fixtures. The library's primary use case — managing audit triggers across many
tables — has never been validated end-to-end. The tmp-research identified two common audit patterns (Shape A: one
function per table; Shape B: shared function with per-table triggers) and several canonicalization edge cases (SECURITY
DEFINER, default arguments, multi-schema setups) that remain untested. Without realistic validation, we risk shipping a
pipeline that works on toy examples but breaks on the first real workload.

## What Changes

- Add end-to-end integration tests exercising the full autogenerate pipeline with realistic audit trigger patterns:
  - **Shape A** (per-table function + trigger): N tables, each with a dedicated audit function and trigger.
  - **Shape B** (shared function + per-table triggers): 1 shared audit function, N triggers referencing it.
- Test lifecycle scenarios beyond simple create: modifying an audit function body, adding a trigger for a new table,
  removing a trigger for a dropped table, and the no-op case where nothing changed.
- Test edge cases from research: `SECURITY DEFINER` functions, multi-schema setups, and `CREATE OR REPLACE` updates that
  change function behavior.
- Validate dependency ordering under realistic conditions (function creates before trigger creates; trigger drops before
  function drops).
- Verify generated migration files are executable: run `upgrade()` and `downgrade()` against a live database and confirm
  the schema reaches the expected state.

## Non-goals

- No new library features or API changes — this is a pure test/validation change.
- No support for new object types (views, types, policies).
- No performance benchmarking (hundreds of entities) — focus is correctness, not scale.
- No changes to the `--sql` offline mode (requires a fundamentally different approach).
- Not resolving which audit pattern (A vs B) is "better" — test both, support both.

## Capabilities

### New Capabilities

- `e2e-audit-patterns`: End-to-end integration test suite exercising realistic audit trigger patterns (Shape A and Shape
  B) through the full autogenerate pipeline, including lifecycle scenarios, edge cases, and migration executability.

### Modified Capabilities

_(none — this change adds tests only, no requirement changes to existing specs)_

## Impact

- **Tests**: New integration test module(s) in `tests/alembic_pg_autogen/`.
- **Fixtures**: May need shared helper fixtures for creating multi-table audit setups.
- **CI**: Integration tests require Docker/PostgreSQL (already configured via testcontainers).
- **Source**: No changes to `src/` unless tests expose bugs that need fixing. If bugs are found, they'll be tracked as
  separate follow-up changes.
