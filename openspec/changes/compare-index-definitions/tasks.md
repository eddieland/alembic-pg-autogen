## 1. Catalog Inspection Layer

- [x] 1.1 Add `IndexInfo` NamedTuple to `src/alembic_pg_autogen/inspect.py` with fields
  `(schema, table_name, name, unique, shape)`
- [x] 1.2 Add `_INDEXES_QUERY` reading `pg_get_indexdef()` and stripping the identity prefix in SQL, rebuilding it with
  `quote_ident()` and verifying it with `left()` before stripping
- [x] 1.3 Exclude constraint-backed indexes (`pg_constraint.conindid` with `contype IN ('p','u','x')`) and
  extension-owned indexes
- [x] 1.4 Add `inspect_indexes(conn, schemas=None, table_names=None)`, omitting rows whose prefix did not match and
  logging a warning
- [x] 1.5 Add inspection tests to `tests/alembic_pg_autogen/test_inspect.py` covering identity, shape for every feature,
  schema and table filtering, constraint-backed exclusion, identifiers containing `USING`, empty result

## 2. Canonicalization Layer

- [x] 2.1 Add `canonicalize_indexes(conn, *, schema, table_name, indexes)` to `src/alembic_pg_autogen/canonicalize.py`
- [x] 2.2 Create a `TEMP` clone named after the target table and execute the compiled `CreateIndex` under
  `schema_translate_map={None: "pg_temp", schema: "pg_temp"}`
- [x] 2.3 Read probes back with the same prefix-stripping deparse the inspector uses, returning `IndexInfo` carrying the
  caller's schema and table
- [x] 2.4 Isolate each probe in its own nested savepoint so one unusable index does not cost the rest their comparison
- [x] 2.5 Add canonicalization tests to `tests/alembic_pg_autogen/test_canonicalize.py` covering round-trip equality,
  predicate rewrites, qualified and unqualified metadata, unusable indexes, savepoint cleanliness, real table untouched

## 3. Comparator

- [x] 3.1 Add `src/alembic_pg_autogen/compare_indexes.py` with a table-level comparator registered under
  `qualifier="postgresql"` and `priority=DispatchPriority.LAST`
- [x] 3.2 Collect named metadata indexes and restrict to names shared with the catalog
- [x] 3.3 Skip indexes for which an operation is already present in `modify_table_ops`
- [x] 3.4 Emit `DropIndexOp.from_index()` + `CreateIndexOp.from_index()` for differing definitions, honoring
  `include_name` and `include_object` filters
- [x] 3.5 Add comparator tests to `tests/alembic_pg_autogen/test_indexes.py` covering helper units, short-circuits,
  plugin registration, and end-to-end autogenerate for every gap case and every equivalent case

## 4. Concurrent Rendering

- [x] 4.1 Add `CreateIndexConcurrentlyOp` and `DropIndexConcurrentlyOp` to `src/alembic_pg_autogen/ops.py`
- [x] 4.2 Add renderers to `src/alembic_pg_autogen/render.py` delegating to `render_op()` inside
  `op.get_context().autocommit_block()`
- [x] 4.3 Gate the wrapping on the `pg_index_concurrently` autogenerate option
- [x] 4.4 Add rendering tests, including applying a generated concurrent migration

## 5. Packaging and Wiring

- [x] 5.1 Register `alembic_pg_autogen.indexes` as a third plugin in `src/alembic_pg_autogen/__init__.py`
- [x] 5.2 Export `IndexInfo`, `inspect_indexes`, `canonicalize_indexes`, `CreateIndexConcurrentlyOp`, and
  `DropIndexConcurrentlyOp`
- [x] 5.3 Update export tests in `tests/alembic_pg_autogen/test_import.py`

## 6. Documentation

- [x] 6.1 Document index support in `README.md` and `docs/quickstart.rst`, including the concurrent opt-in and how to
  disable the plugin
- [x] 6.2 Run `make lint` and fix any issues
- [x] 6.3 Run the full test suite against PostgreSQL and verify all existing and new tests pass
