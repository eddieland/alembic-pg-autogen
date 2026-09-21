# Tasks

## 1. Catalog inspector

- [x] 1.1 Append `validated: bool = True` to `CheckConstraintInfo` in `src/alembic_pg_autogen/inspect.py` and read
  `con.convalidated` in `_CHECK_CONSTRAINTS_QUERY`. Verify with unit tests for the default and integration tests for a
  `NOT VALID` constraint in `tests/alembic_pg_autogen/test_inspect.py`.

## 2. Value set classification

- [x] 2.1 Create `src/alembic_pg_autogen/value_sets.py` with `ValueSet`, `ValueSetChange`, `parse_value_set`,
  `compare_value_sets`, and `classify_value_set_change`, parsing with postgast. Verify with
  `tests/alembic_pg_autogen/test_value_sets.py` covering both shapes, casts, negations, non-string literals, and each
  classification.

## 3. Operations and renderers

- [x] 3.1 Add `ValidateConstraintOp`, `CreateCheckConstraintNotValidOp`, and `NoOp` to `src/alembic_pg_autogen/ops.py`.
  Verify with `tests/alembic_pg_autogen/test_ops.py` covering fields, `reverse()`, `to_diff_tuple()`, and the forced
  `postgresql_not_valid` keyword.
- [x] 3.2 Add renderers for the three operations to `src/alembic_pg_autogen/render.py`. Verify with
  `tests/alembic_pg_autogen/test_render.py` covering schema quoting, the appended keyword, the backfill comment, batch
  mode, and the `pass` line.

## 4. Comparator

- [x] 4.1 In `src/alembic_pg_autogen/compare_check_constraints.py`, keep type-bound constraints whose `_create_rule`
  accepts PostgreSQL, and compile expressions with `include_table=False`. Verify with unit tests for `Enum`, `Boolean`,
  and native `Enum` columns in `tests/alembic_pg_autogen/test_check_constraints.py`.
- [x] 4.2 Read `pg_check_constraint_validation` from `autogen_context.opts`, raise `ValueError` on an unknown value, and
  add the key to the recognized keys in `src/alembic_pg_autogen/compare.py`. Verify with unit tests for the default, the
  invalid value, and the misspelling warning in `tests/alembic_pg_autogen/test_compare_options.py`.
- [x] 4.3 Emit `ValidateConstraintOp` for an unchanged expression whose catalog row is `NOT VALID` and whose metadata
  constraint does not declare `postgresql_not_valid`. Verify with stubbed-catalog unit tests.
- [x] 4.4 Classify each changed expression and emit `CreateCheckConstraintNotValidOp` for a value set change in
  `"deferred"` mode or for a metadata constraint that declares `postgresql_not_valid`, with `column` and
  `removed_values` set. Keep the plain drop and add pair otherwise. Carry the catalog validation state on the reflected
  constraint. Verify with stubbed-catalog unit tests for each branch.
- [x] 4.5 Register the comparator with `priority=DispatchPriority.LAST`, discard the `DropConstraintOp` that Alembic's
  plugin emits for a type-bound constraint the model still declares, and add a type-bound constraint the catalog lacks.
  Verify with stubbed-catalog unit tests and an integration test that enables
  `alembic.autogenerate.checkconstraint_byname` explicitly.
- [x] 4.6 Add integration tests in `tests/alembic_pg_autogen/test_check_constraints.py`: a non-native `Enum` widening
  renders `postgresql_not_valid=True`, a narrowing renders the backfill comment, the second run emits the validation and
  the third run emits nothing, and `"immediate"` mode keeps one validating statement.

## 5. Packaging and documentation

- [x] 5.1 Export the new operations and the classifier from `src/alembic_pg_autogen/__init__.py`. Verify with
  `tests/alembic_pg_autogen/test_import.py`, including the renderer registration subprocess test.
- [x] 5.2 Document non-native `Enum` support, the two-revision flow, the backfill comment, and
  `pg_check_constraint_validation` in `README.md` and `docs/quickstart.rst`. Verify with
  `uv run mdformat --check README.md CLAUDE.md openspec/`.
- [x] 5.3 Run `make lint` and the full test suite against PostgreSQL, and verify that every check passes.
