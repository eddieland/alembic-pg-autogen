## MODIFIED Requirements

### Requirement: Bulk-load function definitions from PostgreSQL catalog

The module SHALL provide an `inspect_functions` function that queries `pg_proc` joined with `pg_namespace` to retrieve
all user-defined functions and procedures. It SHALL use `pg_get_functiondef(oid)` to obtain canonical DDL for each
function. It SHALL return a sequence of `FunctionInfo` NamedTuple instances. It SHALL exclude functions owned by any
PostgreSQL extension by filtering out rows where `pg_depend` contains a dependency with `deptype = 'e'` and
`classid = 'pg_catalog.pg_proc'::regclass`.

#### Scenario: Load all functions from default schemas

- **WHEN** `inspect_functions(conn)` is called without specifying schemas
- **THEN** it returns `FunctionInfo` instances for all functions in schemas other than `pg_catalog` and
  `information_schema`
- **AND** each `FunctionInfo` contains `schema`, `name`, `identity_args`, and `definition` fields

#### Scenario: Load functions from specific schemas

- **WHEN** `inspect_functions(conn, schemas=["public", "audit"])` is called
- **THEN** it returns `FunctionInfo` instances only for functions in the `public` and `audit` schemas
- **AND** functions in other user schemas are excluded

#### Scenario: Aggregates and window functions are excluded

- **WHEN** the database contains aggregate functions (`prokind = 'a'`) or window functions (`prokind = 'w'`)
- **THEN** `inspect_functions` does not include them in the results
- **AND** only regular functions (`prokind = 'f'`) and procedures (`prokind = 'p'`) are returned

#### Scenario: Extension-owned functions are excluded

- **WHEN** a PostgreSQL extension (e.g., PostGIS) has been created in a managed schema
- **AND** the extension installs functions into that schema (e.g., `ST_Area`, `ST_Buffer`)
- **THEN** `inspect_functions` does not include any extension-owned functions in the results
- **AND** the exclusion is determined by the presence of a `pg_depend` row with `deptype = 'e'` linking the function's
  OID to an extension

#### Scenario: User functions coexist with extension functions

- **WHEN** a schema contains both extension-owned functions and user-defined functions
- **THEN** `inspect_functions` returns only the user-defined functions
- **AND** extension-owned functions are excluded regardless of their name or signature

#### Scenario: Overloaded functions are individually represented

- **WHEN** a schema contains two functions with the same name but different argument types (e.g., `my_func(integer)` and
  `my_func(text, integer)`)
- **THEN** `inspect_functions` returns a separate `FunctionInfo` for each overload
- **AND** their `identity_args` fields differ (e.g., `"integer"` vs `"text, integer"`)

#### Scenario: Empty result when no functions exist

- **WHEN** `inspect_functions` is called on a database with no user-defined functions
- **THEN** it returns an empty sequence

### Requirement: Bulk-load trigger definitions from PostgreSQL catalog

The module SHALL provide an `inspect_triggers` function that queries `pg_trigger` joined with `pg_class` and
`pg_namespace` to retrieve all user-defined triggers. It SHALL use `pg_get_triggerdef(oid)` to obtain canonical DDL for
each trigger. It SHALL return a sequence of `TriggerInfo` NamedTuple instances. It SHALL exclude triggers owned by any
PostgreSQL extension by filtering out rows where `pg_depend` contains a dependency with `deptype = 'e'` and
`classid = 'pg_catalog.pg_trigger'::regclass`.

#### Scenario: Load all triggers from default schemas

- **WHEN** `inspect_triggers(conn)` is called without specifying schemas
- **THEN** it returns `TriggerInfo` instances for all non-internal triggers on tables in schemas other than `pg_catalog`
  and `information_schema`
- **AND** each `TriggerInfo` contains `schema`, `table_name`, `trigger_name`, and `definition` fields

#### Scenario: Load triggers from specific schemas

- **WHEN** `inspect_triggers(conn, schemas=["public"])` is called
- **THEN** it returns `TriggerInfo` instances only for triggers on tables in the `public` schema

#### Scenario: Internal triggers are excluded

- **WHEN** the database contains internal triggers (created by constraints, where `tgisinternal = true`)
- **THEN** `inspect_triggers` does not include them in the results

#### Scenario: Extension-owned triggers are excluded

- **WHEN** a PostgreSQL extension installs triggers in a managed schema
- **THEN** `inspect_triggers` does not include any extension-owned triggers in the results
- **AND** the exclusion is determined by the presence of a `pg_depend` row with `deptype = 'e'` linking the trigger's
  OID to an extension

#### Scenario: Empty result when no triggers exist

- **WHEN** `inspect_triggers` is called on a database with no user-defined triggers
- **THEN** it returns an empty sequence

### Requirement: Single query per object type

Both inspect functions SHALL execute catalog queries using `sqlalchemy.text()` with raw SQL against PostgreSQL system
catalog tables. The extension-ownership check SHALL be performed within the same SQL query (via a `NOT EXISTS` subquery
against `pg_depend`), not as a separate query or Python-side filter.

#### Scenario: Single query for functions including extension filter

- **WHEN** `inspect_functions` is called
- **THEN** it executes exactly one SQL query that both retrieves function definitions and excludes extension-owned
  functions

#### Scenario: Single query for triggers including extension filter

- **WHEN** `inspect_triggers` is called
- **THEN** it executes exactly one SQL query that both retrieves trigger definitions and excludes extension-owned
  triggers
