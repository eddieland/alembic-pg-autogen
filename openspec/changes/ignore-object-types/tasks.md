## 1. Sentinel

- [x] 1.1 Add `src/alembic_pg_autogen/sentinels.py` with the `_IgnoredSentinel` enum, the `IGNORED` value, and the
  `Ignored` literal alias; override `__repr__`/`__str__` so logs read `IGNORED`
- [x] 1.2 Export `IGNORED` and `Ignored` from `src/alembic_pg_autogen/__init__.py` and `__all__`
- [x] 1.3 Add `tests/alembic_pg_autogen/test_sentinels.py` covering singleton identity, `repr`/`str`, and inequality
  with `()` / `[]`; extend `tests/alembic_pg_autogen/test_import.py` with the new exports

## 2. Canonicalization Layer

- [x] 2.1 Widen `canonicalize()`'s `function_ddl` / `view_ddl` / `trigger_ddl` parameters to `Sequence[str] | Ignored`
  in `src/alembic_pg_autogen/canonicalize.py`
- [x] 2.2 Add the `_declared()` helper that resolves `IGNORED` to `()` for execution, logging, and warning checks
- [x] 2.3 Skip `inspect_functions` / `inspect_views` / `inspect_triggers` in the readback for ignored types
- [x] 2.4 Have `canonicalize_functions` / `canonicalize_views` / `canonicalize_triggers` pass `IGNORED` for the object
  types they do not return
- [x] 2.5 Add integration tests to `tests/alembic_pg_autogen/test_canonicalize.py` — ignored types are not read back,
  empty sequences still are

## 3. Comparator Integration

- [x] 3.1 Pass `IGNORED` through `_resolve_ddl()` in `src/alembic_pg_autogen/compare.py` unchanged
- [x] 3.2 Skip `inspect_*` calls for ignored object types when building the current state
- [x] 3.3 Short-circuit with `PriorityDispatchResult.CONTINUE` when all three keys are `IGNORED`, and log the ignored
  types at `INFO` otherwise
- [x] 3.4 Return an empty desired set per ignored type in `_filter_to_declared()` without parsing DDL or reading
  `current_schema()`, and suppress the "no canonical <type> matched" warning for it
- [x] 3.5 Add unit tests for `_resolve_ddl(IGNORED)` and `_filter_to_declared` with every type ignored (no connection
  required)
- [x] 3.6 Add integration tests to `tests/alembic_pg_autogen/test_autogenerate.py` — ignored views/functions/triggers
  are not dropped, all-ignored emits no operations

## 4. Documentation

- [x] 4.1 Add an "Ignoring an object type" section to `README.md`
- [x] 4.2 Add an "Ignoring an object type" step to `docs/quickstart.rst`, including the empty-sequence contrast and the
  `collect_view_ddl() or IGNORED` idiom
- [x] 4.3 Run `make lint` and fix any issues
- [x] 4.4 Run `make test` and verify all existing and new tests pass
