# Tasks

## 1. Canonicalization

- [x] 1.1 Add `choose_check_constraint_names` to `src/alembic_pg_autogen/canonicalize.py`, with one nested savepoint per
  expression. Verify with unit and integration tests in `tests/alembic_pg_autogen/test_canonicalize.py` for the naming
  rule, suffixes, rollback, a rejected expression, a missing table, and empty input.

## 2. Comparator

- [x] 2.1 Add `_unnamed_check_constraints` to `src/alembic_pg_autogen/compare_check_constraints.py`. Verify with unit
  tests for table and column constraints, named constraints, naming conventions, unnamed `Enum`, and `Boolean`.
- [x] 2.2 Pair unnamed constraints by canonical expression, discard Alembic's drops for pairs, and emit
  `ValidateConstraintOp` for a `NOT VALID` pair. Verify with stubbed-catalog unit tests in
  `tests/alembic_pg_autogen/test_check_constraints.py`, including duplicate expressions and failed probes.
- [x] 2.3 Add each unpaired constraint under the chosen name, with a `name` keyword on `_create_check_constraint_op`.
  Verify with stubbed-catalog unit tests for the add, its reverse, `NOT VALID`, filters, and a failed name probe.
- [x] 2.4 Add integration tests: the proposal model with Alembic's plugin enabled, an add with upgrade and downgrade, a
  `NOT VALID` pair, an extra catalog copy, and a constraint on a new column.

## 3. Packaging and documentation

- [x] 3.1 Export `choose_check_constraint_names` from `src/alembic_pg_autogen/__init__.py`. Verify with
  `tests/alembic_pg_autogen/test_import.py`.
- [x] 3.2 Document unnamed constraints in `README.md`, `docs/quickstart.rst`, and the module docstring. Verify with
  `uv run mdformat --check README.md CLAUDE.md openspec/`.
- [x] 3.3 Run `make lint` and the full test suite against PostgreSQL, and verify that every check passes.

## 4. Review fixes

- [x] 4.1 Run both probe functions on an empty `TEMP` copy in `src/alembic_pg_autogen/canonicalize.py`, with one nested
  savepoint per probe, escaped colons, and a warning for a connection without a transaction. Verify with integration
  tests for an open reader under `lock_timeout`, a rejected expression, literals with `:` and `%`, and `AUTOCOMMIT`.
- [x] 4.2 Choose names against every constraint name in the schema and against `reserved`. Verify with integration tests
  for a name on another table and for reserved names.
- [x] 4.3 Undo the `%` doubling in `_compile_check_expression`, and add `CreateCheckConstraintTextOp` and
  `DropCheckConstraintOp` to `src/alembic_pg_autogen/ops.py` with a renderer in `src/alembic_pg_autogen/render.py`.
  Verify with render tests on a psycopg dialect and an integration test whose downgrade restores `%` and `:`.
- [x] 4.4 Pair a candidate that `include_name` excludes, keep the leftover drops after a failed probe, match unpaired
  value sets on one column, and pass the planned constraint names as `reserved`. Verify with stubbed-catalog unit tests
  and integration tests for each review scenario.
- [x] 4.5 Append `postgresql_not_valid` only when Alembic did not render it, and leave the default `False` out of adds.
  Verify the full suite on Alembic 1.19.1 and 1.20.1.
