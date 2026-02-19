## 1. Add `pg_depend` exclusion to inspection queries

- [x] 1.1 Add
  `NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend d WHERE d.classid = 'pg_catalog.pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')`
  clause to `_FUNCTIONS_QUERY` in `src/alembic_pg_autogen/_inspect.py`
- [x] 1.2 Add
  `NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend d WHERE d.classid = 'pg_catalog.pg_trigger'::regclass AND d.objid = t.oid AND d.deptype = 'e')`
  clause to `_TRIGGERS_QUERY` in `src/alembic_pg_autogen/_inspect.py`
- [x] 1.3 Run existing tests (`make test`) to verify no regressions — existing tests use user-defined functions only, so
  the new clause should be transparent

## 2. PostGIS test infrastructure

- [x] 2.1 Register `postgis` as a custom pytest marker in `tests/alembic_pg_autogen/conftest.py` (or `pyproject.toml`)
  so `pytest -m postgis` and `pytest -m "not postgis"` work without warnings
- [x] 2.2 Create `tests/alembic_pg_autogen/test_e2e_postgis.py` with a session-scoped `postgis_engine` fixture using
  `PostgresContainer("postgis/postgis:{pg_version}-3.5", driver="psycopg")`

## 3. PostGIS end-to-end tests

- [x] 3.1 Add test: `CREATE EXTENSION postgis` in a test schema, create a user function in the same schema, call
  `inspect_functions`, assert user function present and no `ST_*` functions returned
- [x] 3.2 Add test: verify a well-known PostGIS function (e.g., `ST_Area`) exists in `pg_proc` via direct query but is
  absent from `inspect_functions` results
- [x] 3.3 Add test: with PostGIS installed, create a user trigger in the same schema, call `inspect_triggers`, assert
  user trigger present and no extension-owned triggers returned
- [x] 3.4 Run `make lint` and `make test` (with PostGIS tests included) to confirm everything passes
