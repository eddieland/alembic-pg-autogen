## 1. Catalog Inspection Layer

- [ ] 1.1 Add `ViewInfo` NamedTuple to `src/alembic_pg_autogen/_inspect.py` with fields `(schema, name, definition)`
- [ ] 1.2 Add `_VIEWS_QUERY` SQL constant that queries `pg_class` + `pg_namespace` with `relkind = 'v'`, reconstructs
  full DDL using `quote_ident()` + `pg_get_viewdef(oid, true)`
- [ ] 1.3 Add `inspect_views(conn, schemas=None) -> Sequence[ViewInfo]` function following `inspect_functions` pattern
- [ ] 1.4 Add view inspection tests to `tests/alembic_pg_autogen/test_inspect.py` — create views in test DB, verify
  `inspect_views` returns correct `ViewInfo` instances with reconstructed DDL, schema filtering, empty result

## 2. Canonicalization Layer

- [ ] 2.1 Add `views` field to `CanonicalState` NamedTuple in `src/alembic_pg_autogen/_canonicalize.py` (append after
  `triggers`)
- [ ] 2.2 Add `view_ddl` parameter to `canonicalize()` — execute view DDL after functions, before triggers; call
  `inspect_views` in the readback
- [ ] 2.3 Add `canonicalize_views()` convenience wrapper following `canonicalize_functions` pattern
- [ ] 2.4 Add view canonicalization tests to `tests/alembic_pg_autogen/test_canonicalize.py` — round-trip view DDL,
  verify canonical form, test function-referencing views, test `CREATE OR REPLACE VIEW`

## 3. Diff Layer

- [ ] 3.1 Change `_diff_items` in `src/alembic_pg_autogen/_diff.py` to use `item[:-1]` instead of `item[:3]` for
  identity keys
- [ ] 3.2 Add `ViewOp` NamedTuple to `src/alembic_pg_autogen/_diff.py` following `FunctionOp`/`TriggerOp` pattern
- [ ] 3.3 Add `view_ops` field to `DiffResult` NamedTuple (append after `trigger_ops`)
- [ ] 3.4 Update `diff()` to call `_diff_items` for views and populate `view_ops`
- [ ] 3.5 Add view diff tests to `tests/alembic_pg_autogen/test_diff.py` — create/replace/drop view ops, identity
  matching by `(schema, name)`, ordering, mixed operations; verify existing function/trigger tests still pass with
  `item[:-1]` change

## 4. Operations Layer

- [ ] 4.1 Add `CreateViewOp`, `ReplaceViewOp`, `DropViewOp` classes to `src/alembic_pg_autogen/_ops.py` following
  existing trigger op pattern — each implements `reverse()` and `to_diff_tuple()`
- [ ] 4.2 Add view operation tests to `tests/alembic_pg_autogen/test_ops.py` — construction, `reverse()`, and
  `to_diff_tuple()` for all three view op types

## 5. Render Layer

- [ ] 5.1 Add renderers for `CreateViewOp`, `ReplaceViewOp`, `DropViewOp` to `src/alembic_pg_autogen/_render.py` —
  create/replace use `_render_execute(definition)`, drop uses `DROP VIEW schema.name`
- [ ] 5.2 Add view render tests to `tests/alembic_pg_autogen/test_render.py` — verify rendered output for all three op
  types, DDL quoting with single quotes

## 6. Comparator Integration

- [ ] 6.1 Add `_VIEW_RE` regex to `src/alembic_pg_autogen/_compare.py` for extracting `(schema, name)` from view DDL
- [ ] 6.2 Add `_parse_view_names()` helper following `_parse_function_names` pattern
- [ ] 6.3 Update `_compare_pg_objects()` to read `pg_views` from opts, call `inspect_views`, include views in
  `CanonicalState` construction, and call `_filter_to_declared` with view identities
- [ ] 6.4 Update `_filter_to_declared()` and `_filter_to_schemas()` to handle views
- [ ] 6.5 Update `_order_ops()` to accept `view_ops` and emit 6-group dependency-safe ordering (drop triggers → drop
  views → drop functions → create/replace functions → create/replace views → create/replace triggers)
- [ ] 6.6 Add view comparator tests to `tests/alembic_pg_autogen/test_autogenerate.py` — e2e autogenerate with views,
  views + functions together, view-only changes

## 7. Public Exports

- [ ] 7.1 Update `src/alembic_pg_autogen/__init__.py` — add imports and `__all__` entries for `ViewInfo`, `ViewOp`,
  `inspect_views`, `canonicalize_views`, `CreateViewOp`, `ReplaceViewOp`, `DropViewOp`
- [ ] 7.2 Update import tests in `tests/alembic_pg_autogen/test_import.py` to verify all new public exports

## 8. Integration Tests

- [ ] 8.1 Add view-focused e2e integration tests — create a test with multiple views (some referencing functions),
  verify autogenerate detects create/replace/drop correctly, verify migration file renders valid Python
- [ ] 8.2 Run `make lint` and fix any issues
- [ ] 8.3 Run full `make test` and verify all existing + new tests pass
