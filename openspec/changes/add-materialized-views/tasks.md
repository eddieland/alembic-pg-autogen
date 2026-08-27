## 1. Inspection

- [ ] 1.1 Add `MaterializedViewInfo` and `inspect_materialized_views()` to `src/alembic_pg_autogen/inspect.py`, with the
  `relkind = 'm'` query, `quote_ident()` reconstruction, and the `pg_depend` extension filter. Verify with new tests in
  `tests/alembic_pg_autogen/test_inspect.py` covering load, schema scoping, view/matview separation, and the
  no-data-clause definition shape.
- [ ] 1.2 Add `inspect_matview_indexes()` to `src/alembic_pg_autogen/inspect.py` returning sorted `pg_get_indexdef()`
  statements. Verify with tests covering a unique index, several indexes, and no indexes.

## 2. Canonicalization

- [ ] 2.1 Add `matview_ddl` to `canonicalize()` in `src/alembic_pg_autogen/canonicalize.py`: parse identities from
  `CreateTableAsStmt`, execute drop-first `DROP MATERIALIZED VIEW IF EXISTS ... CASCADE`, force `WITH NO DATA` via
  `postgast.deparse()`, execute after views, read back with `inspect_materialized_views`, and extend `CanonicalState`
  with `materialized_views`. Verify with tests in `tests/alembic_pg_autogen/test_canonicalize.py` covering the
  drop-first path over an existing object, the raising-function query, and `IGNORED`.
- [ ] 2.2 Add a rollback-safety test proving a pre-existing populated materialized view keeps its definition, index, and
  `relispopulated` state after canonicalization.
- [ ] 2.3 Add `canonicalize_materialized_views()` wrapper and verify it reads back only the materialized view catalog.

## 3. Diff

- [ ] 3.1 Add `MaterializedViewOp` and the `materialized_view_ops` field to `src/alembic_pg_autogen/diff.py`. Verify
  with tests in `tests/alembic_pg_autogen/test_diff.py` covering create, replace, drop, no-op, sorting, and the
  view-versus-matview independence scenario.

## 4. Operations and rendering

- [ ] 4.1 Add `CreateMaterializedViewOp`, `ReplaceMaterializedViewOp` (with `index_ddl`), and `DropMaterializedViewOp`
  to `src/alembic_pg_autogen/ops.py`. Verify `reverse()` round-trips and `to_diff_tuple()` values in
  `tests/alembic_pg_autogen/test_ops.py`.
- [ ] 4.2 Add the three renderers to `src/alembic_pg_autogen/render.py`, building `DROP MATERIALIZED VIEW` from the
  identifier preparer and emitting `index_ddl` statements on replace. Verify in
  `tests/alembic_pg_autogen/test_render.py`, including the quoted-identifier case and the empty `index_ddl` case.

## 5. Comparator integration

- [ ] 5.1 Wire `pg_materialized_views` through `src/alembic_pg_autogen/compare.py`: resolve the opt, inspect current
  state, pass `matview_ddl`, filter declared identities via `CreateTableAsStmt` with `objtype = OBJECT_MATVIEW`, attach
  `inspect_matview_indexes()` output to each replace, and extend `_order_ops` to 8 groups. Verify with tests in
  `tests/alembic_pg_autogen/test_compare_options.py` and `test_compare_helpers.py`, including the typo warning for
  `pg_materialized_view`.
- [ ] 5.2 Export the new public names from `src/alembic_pg_autogen/__init__.py` and verify
  `tests/alembic_pg_autogen/test_import.py` passes.

## 6. End-to-end and documentation

- [ ] 6.1 Add autogenerate e2e tests in `tests/alembic_pg_autogen/test_autogenerate.py`: create, replace with index
  preservation, drop, unchanged no-op, and upgrade/downgrade execution of the generated migration against a live
  database.
- [ ] 6.2 Document `pg_materialized_views` in `README.md` and `docs/`, including the replacement lifecycle, index
  preservation, the populate-on-migrate behavior, and the view-over-matview ordering limitation. Verify with
  `uv run mdformat --check README.md CLAUDE.md openspec/` and `make lint`.
