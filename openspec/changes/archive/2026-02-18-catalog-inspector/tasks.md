## 1. Data types

- [x] 1.1 Create `src/alembic_pg_autogen/_inspect.py` with `FunctionInfo` and `TriggerInfo` dataclasses (slots, frozen).
  Fields per spec: `schema`, `name`, `identity_args`, `definition` for functions; `schema`, `table_name`,
  `trigger_name`, `definition` for triggers.
- [x] 1.2 Export `FunctionInfo`, `TriggerInfo`, `inspect_functions`, `inspect_triggers` from `__init__.py` and add to
  `__all__`.

## 2. Catalog queries

- [x] 2.1 Implement `inspect_functions(conn, schemas=None)` in `_inspect.py`. Query `pg_proc` joined with
  `pg_namespace`, filter `prokind IN ('f', 'p')`, use `pg_get_functiondef(oid)` for definitions, use `format_type()` on
  `proargtypes` for `identity_args`. Accept optional `schemas` sequence; default excludes `pg_catalog` and
  `information_schema`. Single query via `sqlalchemy.text()`.
- [x] 2.2 Implement `inspect_triggers(conn, schemas=None)` in `_inspect.py`. Query `pg_trigger` joined with `pg_class`
  and `pg_namespace`, filter `NOT tgisinternal`, use `pg_get_triggerdef(oid)` for definitions. Accept optional `schemas`
  sequence; default excludes `pg_catalog` and `information_schema`. Single query via `sqlalchemy.text()`.

## 3. Tests

- [x] 3.1 Add unit tests in `tests/test_inspect.py` for `FunctionInfo` and `TriggerInfo` construction and field access.
- [x] 3.2 Add integration test: create a simple SQL function in the test database, call `inspect_functions`, verify the
  returned `FunctionInfo` has correct `schema`, `name`, `identity_args`, and a non-empty `definition` from
  `pg_get_functiondef()`.
- [x] 3.3 Add integration test: create overloaded functions (same name, different arg types), verify `inspect_functions`
  returns separate `FunctionInfo` instances with distinct `identity_args`.
- [x] 3.4 Add integration test: create an aggregate function, verify `inspect_functions` excludes it.
- [x] 3.5 Add integration test: create a table with a trigger, call `inspect_triggers`, verify the returned
  `TriggerInfo` has correct `schema`, `table_name`, `trigger_name`, and a non-empty `definition` from
  `pg_get_triggerdef()`.
- [x] 3.6 Add integration test: verify constraint-created internal triggers are excluded from `inspect_triggers`
  results.
- [x] 3.7 Add integration test: call `inspect_functions` and `inspect_triggers` with `schemas=["nonexistent"]`, verify
  empty results.
- [x] 3.8 Add integration test: call both inspect functions with explicit `schemas=["public"]` parameter, verify only
  public-schema objects are returned.

## 4. Quality

- [x] 4.1 Run `make lint` and fix any ruff, basedpyright, or codespell issues.
- [x] 4.2 Run `make test` and verify all tests pass (unit and integration).
