## 1. Catalog Inspection Layer

- [x] 1.1 Add `CheckConstraintInfo` NamedTuple to `src/alembic_pg_autogen/inspect.py` with fields
  `(schema, table_name, name, expression)`
- [x] 1.2 Add `_CHECK_CONSTRAINTS_QUERY` selecting `contype = 'c'` constraints with
  `pg_get_expr(conbin, conrelid, true)`, excluding extension-owned constraints
- [x] 1.3 Add `inspect_check_constraints(conn, schemas=None, table_names=None)` following the `inspect_views` pattern
- [x] 1.4 Add `current_schema(conn)` and replace the private `_get_default_schema()` helper in
  `src/alembic_pg_autogen/compare.py` with it
- [x] 1.5 Add inspection tests to `tests/alembic_pg_autogen/test_inspect.py` — identity, normalized expression, schema
  and table filtering, exclusion of `NOT NULL`, primary key, and domain constraints, empty result

## 2. Canonicalization Layer

- [x] 2.1 Add `canonicalize_check_constraints(conn, *, schema, table_name, expressions)` to
  `src/alembic_pg_autogen/canonicalize.py`, probing with prefixed `NOT VALID` constraints inside a savepoint
- [x] 2.2 Read probes back with the same `pg_get_expr()` deparse the inspector uses, mapping probe names back to the
  caller's constraint names
- [x] 2.3 Swallow `DBAPIError`, log a warning, and omit the affected names rather than propagating
- [x] 2.4 Add canonicalization tests to `tests/alembic_pg_autogen/test_canonicalize.py` — round-trip equality,
  parenthesization and `IN (...)` rewrites, changed expressions, violating rows, unusable expressions, savepoint
  cleanliness, `search_path` resolution

## 3. Comparator

- [x] 3.1 Add `src/alembic_pg_autogen/compare_check_constraints.py` with a table-level comparator registered under
  `qualifier="postgresql"`
- [x] 3.2 Collect named, non-type-bound metadata constraints, including column-level ones, resolving naming conventions
  via `format_constraint()`
- [x] 3.3 Compile metadata expressions with `literal_binds`, skipping any that fail to compile
- [x] 3.4 Skip the round-trip when the compiled text already matches the catalog modulo whitespace
- [x] 3.5 Emit `DropConstraintOp.from_constraint()` + `AddConstraintOp.from_constraint()` for differing expressions,
  honoring `include_name` and `include_object` filters
- [x] 3.6 Add comparator tests to `tests/alembic_pg_autogen/test_check_constraints.py` — helper units, short-circuits,
  plugin registration, and end-to-end autogenerate for changed, equivalent, and rewritten expressions

## 4. Packaging and Wiring

- [x] 4.1 Register `alembic_pg_autogen.checkconstraints` as a second plugin in `src/alembic_pg_autogen/__init__.py`
- [x] 4.2 Export `CheckConstraintInfo`, `inspect_check_constraints`, `canonicalize_check_constraints`, and
  `current_schema`
- [x] 4.3 Raise the Alembic floor to `>=1.19` in `pyproject.toml` and refresh `uv.lock`
- [x] 4.4 Update export tests in `tests/alembic_pg_autogen/test_import.py`

## 5. Test Infrastructure

- [x] 5.1 Let `pg_engine` use an existing server via `ALEMBIC_PG_AUTOGEN_TEST_URL`, falling back to testcontainers

## 6. Documentation

- [x] 6.1 Document check constraint support in `README.md` and `docs/quickstart.rst`, including the lock note and how to
  disable the plugin
- [x] 6.2 Run `make lint` and fix any issues
- [x] 6.3 Run the full test suite against PostgreSQL and verify all existing and new tests pass
