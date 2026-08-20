## 1. Comparator Default

- [x] 1.1 Default `pg_functions` / `pg_triggers` / `pg_views` to `IGNORED` in `_compare_pg_objects()`
  (`src/alembic_pg_autogen/compare.py`) and delete the now-redundant "no keys in opts" fast path
- [x] 1.2 Reword the unmanaged-types `INFO` log so it reads correctly for types left unmanaged by omission
- [x] 1.3 Add `tests/alembic_pg_autogen/test_compare_options.py` — empty `opts` and unrelated-only `opts`
  short-circuit without touching the connection

## 2. Typo Detection

- [x] 2.1 Add a `_warn_unrecognized_options()` helper to `src/alembic_pg_autogen/compare.py` using
  `difflib.get_close_matches` against the three recognized keys, called before the short-circuit
- [x] 2.2 Add unit tests to `tests/alembic_pg_autogen/test_compare_options.py` — near miss warns and names the
  intended key; unrelated `pg_*`, recognized, and non-`pg_` keys are silent; a typo warns even when the comparator
  short-circuits

## 3. Test Harness

- [x] 3.1 Change the generated `env.py` in `tests/alembic_pg_autogen/alembic_helpers.py` to forward only the `pg_*`
  attributes actually set, so tests can express a genuinely absent key and a misspelled one
- [x] 3.2 Verify the existing autogenerate tests still pass — the drop tests already pass `[]` explicitly

## 4. Integration Tests

- [x] 4.1 Add `TestAutogenerateAbsentObjectTypes` to `tests/alembic_pg_autogen/test_autogenerate.py` — an omitted `pg_views`
  alongside a declared `pg_functions` emits no `DROP VIEW`, and the same for omitted functions and triggers
- [x] 4.2 Add a regression test that `pg_views=[]` still drops, guarding the empty-sequence/absent distinction
- [x] 4.3 Add an integration test that a misspelled key warns and leaves that type unmanaged

## 5. Documentation

- [x] 5.1 Update the "Ignoring an object type" section of `README.md` — a type is managed once declared; `IGNORED` is
  for recording an explicit opt-out and for conditional values
- [x] 5.2 Update step 5 of `docs/quickstart.rst` the same way, keeping the empty-sequence contrast and the
  `collect_view_ddl() or IGNORED` idiom
- [x] 5.3 Run `make lint` and fix any issues
- [x] 5.4 Run `make test` and verify all existing and new tests pass
