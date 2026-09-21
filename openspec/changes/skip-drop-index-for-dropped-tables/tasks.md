## 1. Rewriter

- [x] 1.1 Add `src/alembic_pg_autogen/rewriters.py` with the `skip_drop_index_for_dropped_tables` `Rewriter`, the
  `_remove_drop_index_for_dropped_tables` rewrite registered for `UpgradeOps` and `DowngradeOps`, and the
  `_dropped_tables` / `_without_drop_index` helpers
- [x] 1.2 Export `skip_drop_index_for_dropped_tables` from `src/alembic_pg_autogen/__init__.py` and `__all__`
- [x] 1.3 Add `tests/alembic_pg_autogen/test_rewriters.py` covering removal inside and outside `ModifyTableOps`,
  `downgrade()`, multi-container scripts, surviving tables, schema matching, non-empty containers, and `chain()`
- [x] 1.4 Extend `tests/alembic_pg_autogen/test_import.py` with the new export

## 2. Integration

- [x] 2.1 Forward a `process_revision_directives` config attribute in `tests/alembic_pg_autogen/alembic_helpers.py`
- [x] 2.2 Add integration tests to `tests/alembic_pg_autogen/test_autogenerate.py`: the default emits `drop_index`, the
  rewriter removes it for a dropped table, keeps it for a surviving table, and trims the `downgrade()` of a created
  table

## 3. Documentation

- [x] 3.1 Add a "Skipping `drop_index` for dropped tables" section to `README.md`
- [x] 3.2 Add a "Skip `drop_index` for dropped tables" step to `docs/quickstart.rst`
- [x] 3.3 Run `make lint` and fix any issues
- [x] 3.4 Run `make test` and verify all existing and new tests pass
