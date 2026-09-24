## 1. Catalog Inspection Layer

- [x] 1.1 Add the `IndexInfo` NamedTuple to `src/alembic_pg_autogen/inspect.py` with the fields
  `(schema, table_name, name, unique, shape)`
- [x] 1.2 Add `_INDEXES_QUERY`. It reads `pg_get_indexdef()` and removes the identity prefix in SQL. It builds the
  prefix with `quote_ident()` and checks it with `left()` before it removes it.
- [x] 1.3 Exclude indexes that implement constraints (`pg_constraint.conindid` with `contype IN ('p','u','x')`) and
  indexes that an extension owns
- [x] 1.4 Add `inspect_indexes(conn, schemas=None, table_names=None)`. Omit rows with no prefix match and log a warning.
- [x] 1.5 Add inspection tests to `tests/alembic_pg_autogen/test_inspect.py` for identity, the shape of each option,
  schema and table filters, the exclusion of constraint indexes, identifiers that contain `USING`, and an empty result

## 2. Canonicalization Layer

- [x] 2.1 Add `canonicalize_indexes(conn, *, schema, table_name, indexes)` to `src/alembic_pg_autogen/canonicalize.py`
- [x] 2.2 Create a `TEMP` copy with the name of the target table. Run the compiled `CreateIndex` under
  `schema_translate_map={None: "pg_temp", schema: "pg_temp"}`.
- [x] 2.3 Read the test indexes back with the same prefix removal as the inspector. Return `IndexInfo` with the schema
  and table of the caller.
- [x] 2.4 Run each test in its own nested savepoint. Thus one bad index does not stop the comparison of the others.
- [x] 2.5 Add canonicalization tests to `tests/alembic_pg_autogen/test_canonicalize.py` for round trip equality,
  predicate normalization, metadata with and without an explicit schema, bad indexes, savepoint cleanup, and no change
  to the real table

## 3. Comparator

- [x] 3.1 Add `src/alembic_pg_autogen/compare_indexes.py` with a table comparator registered under
  `qualifier="postgresql"` and `priority=DispatchPriority.LAST`
- [x] 3.2 Collect the named metadata indexes. Keep only the names that the catalog also has.
- [x] 3.3 Skip indexes that already have an operation in `modify_table_ops`
- [x] 3.4 Emit `DropIndexOp.from_index()` and `CreateIndexOp.from_index()` for different definitions. Apply the
  `include_name` and `include_object` filters.
- [x] 3.5 Add comparator tests to `tests/alembic_pg_autogen/test_indexes.py` for the helper functions, the early
  returns, plugin registration, and a full autogenerate for each missed case and each equivalent case

## 4. Concurrent Rendering

- [x] 4.1 Add `CreateIndexConcurrentlyOp` and `DropIndexConcurrentlyOp` to `src/alembic_pg_autogen/ops.py`
- [x] 4.2 Add renderers to `src/alembic_pg_autogen/render.py`. They call `render_op()` inside
  `op.get_context().autocommit_block()`.
- [x] 4.3 Wrap the operations only when the autogenerate option `pg_index_concurrently` is set
- [x] 4.4 Add rendering tests. Include a test that applies a generated concurrent migration.

## 5. Packaging and Wiring

- [x] 5.1 Register `alembic_pg_autogen.indexes` as a third plugin in `src/alembic_pg_autogen/__init__.py`
- [x] 5.2 Export `IndexInfo`, `inspect_indexes`, `canonicalize_indexes`, `CreateIndexConcurrentlyOp`, and
  `DropIndexConcurrentlyOp`
- [x] 5.3 Update the export tests in `tests/alembic_pg_autogen/test_import.py`

## 6. Documentation

- [x] 6.1 Document index support in `README.md` and `docs/quickstart.rst`. Include the concurrent option and how to
  disable the plugin.
- [x] 6.2 Run `make lint` and fix each problem
- [x] 6.3 Run the full test suite against PostgreSQL. Confirm that all old and new tests pass.
