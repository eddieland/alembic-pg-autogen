## ADDED Requirements

### Requirement: Bulk-load function definitions from PostgreSQL catalog

The module SHALL provide an `inspect_functions` function that queries `pg_proc` joined with `pg_namespace` to retrieve
all user-defined functions and procedures. It SHALL use `pg_get_functiondef(oid)` to obtain canonical DDL for each
function. It SHALL return a sequence of `FunctionInfo` dataclass instances.

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
each trigger. It SHALL return a sequence of `TriggerInfo` dataclass instances.

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

#### Scenario: Empty result when no triggers exist

- **WHEN** `inspect_triggers` is called on a database with no user-defined triggers
- **THEN** it returns an empty sequence

### Requirement: FunctionInfo dataclass

The module SHALL provide a `FunctionInfo` dataclass representing a single PostgreSQL function or procedure as loaded
from the catalog.

#### Scenario: FunctionInfo fields

- **WHEN** a `FunctionInfo` instance is created
- **THEN** it has the following fields:
  - `schema` (`str`): the namespace name from `pg_namespace.nspname`
  - `name` (`str`): the function name from `pg_proc.proname`
  - `identity_args` (`str`): the argument type signature for identity matching, derived from `pg_proc.proargtypes` using
    `format_type()`
  - `definition` (`str`): the complete canonical DDL from `pg_get_functiondef()`

#### Scenario: FunctionInfo identity

- **WHEN** two `FunctionInfo` instances have the same `schema`, `name`, and `identity_args`
- **THEN** they represent the same database function

### Requirement: TriggerInfo dataclass

The module SHALL provide a `TriggerInfo` dataclass representing a single PostgreSQL trigger as loaded from the catalog.

#### Scenario: TriggerInfo fields

- **WHEN** a `TriggerInfo` instance is created
- **THEN** it has the following fields:
  - `schema` (`str`): the table's namespace name from `pg_namespace.nspname`
  - `table_name` (`str`): the table name from `pg_class.relname`
  - `trigger_name` (`str`): the trigger name from `pg_trigger.tgname`
  - `definition` (`str`): the complete canonical DDL from `pg_get_triggerdef()`

#### Scenario: TriggerInfo identity

- **WHEN** two `TriggerInfo` instances have the same `schema`, `table_name`, and `trigger_name`
- **THEN** they represent the same database trigger

### Requirement: SQLAlchemy connection as input

Both `inspect_functions` and `inspect_triggers` SHALL accept a SQLAlchemy `Connection` object as their first argument.
They SHALL NOT create connections, engines, or manage transactions.

#### Scenario: Uses provided connection

- **WHEN** either inspect function is called with a SQLAlchemy `Connection`
- **THEN** it executes catalog queries using that connection
- **AND** it does not create any new connections or engines

#### Scenario: Works within caller's transaction

- **WHEN** the caller has an active transaction on the connection
- **THEN** the inspect functions execute their queries within that existing transaction
- **AND** they do not commit, rollback, or create savepoints

### Requirement: Catalog queries use raw SQL

Both inspect functions SHALL execute catalog queries using `sqlalchemy.text()` with raw SQL against PostgreSQL system
catalog tables (`pg_proc`, `pg_trigger`, `pg_class`, `pg_namespace`, `pg_type`). They SHALL NOT use SQLAlchemy ORM or
reflection APIs for catalog access.

#### Scenario: Single query per object type

- **WHEN** `inspect_functions` is called
- **THEN** it executes exactly one SQL query to retrieve all matching functions

#### Scenario: Single query for triggers

- **WHEN** `inspect_triggers` is called
- **THEN** it executes exactly one SQL query to retrieve all matching triggers
