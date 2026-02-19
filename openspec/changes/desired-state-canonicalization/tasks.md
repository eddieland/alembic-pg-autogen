## 1. Data types and module setup

- [x] 1.1 Create `src/alembic_pg_autogen/_canonicalize.py` with `CanonicalState` NamedTuple. Fields: `functions`
  (`Sequence[FunctionInfo]`), `triggers` (`Sequence[TriggerInfo]`). Import `FunctionInfo` and `TriggerInfo` from
  `_inspect`.
- [x] 1.2 Export `CanonicalState`, `canonicalize`, `canonicalize_functions`, `canonicalize_triggers` from `__init__.py`
  and add to `__all__`.

## 2. Core implementation

- [x] 2.1 Implement `canonicalize(conn, function_ddl=(), trigger_ddl=(), schemas=None)` in `_canonicalize.py`. Open a
  savepoint via `conn.begin_nested()`, execute each function DDL string then each trigger DDL string individually via
  `conn.execute(text(ddl))`, call `inspect_functions(conn, schemas)` and `inspect_triggers(conn, schemas)` to read back
  canonical forms, rollback the savepoint in a `finally` block, and return a `CanonicalState`.
- [x] 2.2 Implement `canonicalize_functions(conn, ddl, schemas=None)` as a thin wrapper that calls
  `canonicalize(conn, function_ddl=ddl, schemas=schemas)` and returns `.functions`.
- [x] 2.3 Implement `canonicalize_triggers(conn, ddl, schemas=None)` as a thin wrapper that calls
  `canonicalize(conn, trigger_ddl=ddl, schemas=schemas)` and returns `.triggers`.

## 3. Tests

- [x] 3.1 Add unit test in `tests/alembic_pg_autogen/test_canonicalize.py` for `CanonicalState` construction and field
  access.
- [x] 3.2 Add integration test: create a function via `canonicalize_functions`, verify the returned `FunctionInfo` has
  canonical DDL from `pg_get_functiondef()` (e.g., pass DDL with extra whitespace, verify canonical output normalizes
  the header).
- [x] 3.3 Add integration test: canonicalize a function and trigger together via `canonicalize`, where the trigger
  references the function. Verify both appear in the result with canonical definitions.
- [x] 3.4 Add integration test: verify the database is unchanged after `canonicalize` — create a function via
  canonicalize, then query `pg_proc` to confirm it does not exist.
- [x] 3.5 Add integration test: pass invalid DDL to `canonicalize`, verify it raises an exception and the connection
  remains usable for subsequent queries.
- [x] 3.6 Add integration test: call `canonicalize` with `schemas=["public"]`, verify results are scoped to that schema.
- [x] 3.7 Add integration test: verify pre-existing functions are included in the result alongside newly canonicalized
  ones (full post-DDL catalog state).
- [x] 3.8 Add integration test: `CREATE OR REPLACE` an existing function with a different body, verify the result
  contains the new canonical definition (not the original).

## 4. Quality

- [x] 4.1 Run `make lint` and fix any ruff, basedpyright, or codespell issues.
- [x] 4.2 Run `make test` and verify all tests pass (unit and integration).
