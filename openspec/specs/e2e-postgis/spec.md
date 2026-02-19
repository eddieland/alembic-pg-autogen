## ADDED Requirements

### Requirement: PostGIS end-to-end test fixture

The test suite SHALL provide a session-scoped `postgis_engine` pytest fixture that creates a PostgreSQL instance using
the `postgis/postgis` Docker image via testcontainers. The fixture SHALL be independent of the existing `pg_engine`
fixture so that non-PostGIS tests are unaffected.

#### Scenario: PostGIS engine is available

- **WHEN** a test requests the `postgis_engine` fixture
- **THEN** it receives a SQLAlchemy `Engine` connected to a PostgreSQL instance with PostGIS available for installation
- **AND** the engine uses the `psycopg` driver

#### Scenario: PostGIS tests use a dedicated marker

- **WHEN** a test is decorated with `@pytest.mark.postgis`
- **THEN** it can be selected or excluded via `pytest -m postgis` or `pytest -m "not postgis"`

### Requirement: Extension functions are excluded from inspection

The test suite SHALL verify that `inspect_functions` excludes extension-owned functions while retaining user-defined
functions in the same schema.

#### Scenario: PostGIS functions excluded, user functions retained

- **WHEN** `CREATE EXTENSION postgis` has been executed in a test schema
- **AND** a user-defined function exists in the same schema
- **AND** `inspect_functions(conn, [schema])` is called
- **THEN** the result contains the user-defined function
- **AND** the result does not contain any PostGIS functions (e.g., `ST_Area`, `ST_Buffer`, `ST_AsText`)

#### Scenario: Well-known PostGIS function confirmed present in catalog

- **WHEN** `CREATE EXTENSION postgis` has been executed in a test schema
- **THEN** a direct query against `pg_proc` for a well-known PostGIS function (e.g., `ST_Area`) in that schema confirms
  the function exists
- **AND** `inspect_functions` does not return it

### Requirement: Extension triggers are excluded from inspection

The test suite SHALL verify that `inspect_triggers` excludes extension-owned triggers while retaining user-defined
triggers in the same schema.

#### Scenario: User triggers retained when extension is present

- **WHEN** `CREATE EXTENSION postgis` has been executed in a test schema
- **AND** a user-defined trigger exists on a table in the same schema
- **AND** `inspect_triggers(conn, [schema])` is called
- **THEN** the result contains the user-defined trigger
- **AND** the result does not contain any extension-owned triggers
