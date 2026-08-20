## ADDED Requirements

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

#### Scenario: Single query for functions including extension filter

- **WHEN** `inspect_functions` is called
- **THEN** it executes exactly one SQL query that both retrieves function definitions and excludes extension-owned
  functions

#### Scenario: Single query for triggers including extension filter

- **WHEN** `inspect_triggers` is called
- **THEN** it executes exactly one SQL query that both retrieves trigger definitions and excludes extension-owned
  triggers

### Requirement: ViewInfo dataclass

The module SHALL provide a `ViewInfo` NamedTuple representing a single PostgreSQL view as loaded from the catalog.

#### Scenario: ViewInfo fields

- **WHEN** a `ViewInfo` instance is created
- **THEN** it has the following fields:
  - `schema` (`str`): the view's namespace name from `pg_namespace.nspname`
  - `name` (`str`): the view name from `pg_class.relname`
  - `definition` (`str`): the complete reconstructed DDL in the form
    `CREATE OR REPLACE VIEW <schema>.<name> AS\n<pg_get_viewdef()>`

#### Scenario: ViewInfo identity

- **WHEN** two `ViewInfo` instances have the same `schema` and `name`
- **THEN** they represent the same database view

#### Scenario: ViewInfo definition includes full DDL

- **WHEN** a view `public.active_users` is inspected
- **THEN** its `definition` field contains `CREATE OR REPLACE VIEW public.active_users AS\n SELECT ...` (the complete
  DDL, not just the query body)

### Requirement: Bulk-load view definitions from PostgreSQL catalog

The module SHALL provide an `inspect_views` function that queries `pg_class` joined with `pg_namespace` to retrieve all
user-defined views. It SHALL use `pg_get_viewdef(oid, true)` for the canonical query body and reconstruct the full DDL
using `quote_ident()` for the schema and view name. It SHALL return a sequence of `ViewInfo` instances.

#### Scenario: Load all views from default schemas

- **WHEN** `inspect_views(conn)` is called without specifying schemas
- **THEN** it returns `ViewInfo` instances for all views in schemas other than `pg_catalog` and `information_schema`
- **AND** each `ViewInfo` contains `schema`, `name`, and `definition` fields

#### Scenario: Load views from specific schemas

- **WHEN** `inspect_views(conn, schemas=["public", "reporting"])` is called
- **THEN** it returns `ViewInfo` instances only for views in the `public` and `reporting` schemas
- **AND** views in other user schemas are excluded

#### Scenario: Only regular views are included

- **WHEN** the database contains both regular views (`relkind = 'v'`) and materialized views (`relkind = 'm'`)
- **THEN** `inspect_views` returns only regular views
- **AND** materialized views are excluded

#### Scenario: Empty result when no views exist

- **WHEN** `inspect_views` is called on a database with no user-defined views
- **THEN** it returns an empty sequence

#### Scenario: Single query execution

- **WHEN** `inspect_views` is called
- **THEN** it executes exactly one SQL query to retrieve all matching views

#### Scenario: SQLAlchemy connection as input

- **WHEN** `inspect_views(conn)` is called with a SQLAlchemy `Connection`
- **THEN** it executes catalog queries using that connection
- **AND** it does not create any new connections or engines

#### Scenario: Definition reconstruction uses quote_ident

- **WHEN** a view exists in a schema or with a name that requires quoting (e.g., mixed-case identifiers)
- **THEN** the reconstructed `definition` uses `quote_ident()` for the schema and view name to ensure proper quoting

### Requirement: CheckConstraintInfo type

The module SHALL provide a `CheckConstraintInfo` NamedTuple representing a single table-level `CHECK` constraint as
loaded from the catalog.

#### Scenario: CheckConstraintInfo fields

- **WHEN** a `CheckConstraintInfo` instance is created
- **THEN** it has the following fields:
  - `schema` (`str`): the constrained table's namespace from `pg_namespace.nspname`
  - `table_name` (`str`): the constrained table from `pg_class.relname`
  - `name` (`str`): the constraint name from `pg_constraint.conname`
  - `expression` (`str`): the normalized check expression from `pg_get_expr(conbin, conrelid, true)`

#### Scenario: CheckConstraintInfo identity

- **WHEN** two `CheckConstraintInfo` instances have the same `schema`, `table_name`, and `name`
- **THEN** they represent the same database constraint
- **AND** `info[:-1]` yields that identity, consistent with the other catalog types

#### Scenario: Payload is an expression, not executable DDL

- **WHEN** a constraint `CHECK (amount >= 0)` on a `numeric` column is inspected
- **THEN** `expression` contains PostgreSQL's deparsed form (e.g. `amount >= 0::numeric`)
- **AND** it does NOT include the `CHECK (...)` wrapper, an `ALTER TABLE` preamble, `NO INHERIT`, or `NOT VALID`

### Requirement: Bulk-load check constraint expressions from PostgreSQL catalog

The module SHALL provide an `inspect_check_constraints` function that queries `pg_constraint` joined with `pg_class` and
`pg_namespace` for constraints with `contype = 'c'`, returning a sequence of `CheckConstraintInfo` instances.

#### Scenario: Load all check constraints from default schemas

- **WHEN** `inspect_check_constraints(conn)` is called without specifying schemas
- **THEN** it returns entries for check constraints in schemas other than `pg_catalog` and `information_schema`

#### Scenario: Load check constraints from specific schemas

- **WHEN** `inspect_check_constraints(conn, schemas=["public"])` is called
- **THEN** only constraints on tables in `public` are returned

#### Scenario: Restrict to specific tables

- **WHEN** `inspect_check_constraints(conn, schemas=["public"], table_names=["orders"])` is called
- **THEN** only constraints on `public.orders` are returned

#### Scenario: Extension-owned constraints are excluded

- **WHEN** a constraint depends on an extension with `pg_depend.deptype = 'e'`
- **THEN** it is excluded from the results

#### Scenario: Non-check constraints are excluded

- **WHEN** a table has primary key, unique, foreign key, or `NOT NULL` constraints
- **THEN** none of them appear in the results, because only `contype = 'c'` is selected

#### Scenario: Domain constraints are excluded

- **WHEN** a domain is defined with a `CHECK` constraint
- **THEN** it is excluded, because a domain constraint has no `conrelid` to join `pg_class` on

#### Scenario: Empty result when no check constraints exist

- **WHEN** `inspect_check_constraints` is called against a schema with no check constraints
- **THEN** it returns an empty sequence

#### Scenario: Single query execution

- **WHEN** `inspect_check_constraints` is called
- **THEN** it executes exactly one SQL query

### Requirement: Resolve the connection's current schema

The module SHALL provide a `current_schema` function returning the connection's `current_schema()`, so that callers
resolving Alembic's `None` schema share one implementation.

#### Scenario: Returns the search_path head

- **WHEN** `current_schema(conn)` is called on a connection whose `search_path` begins with `public`
- **THEN** it returns `"public"`

#### Scenario: Follows a changed search_path

- **WHEN** the connection's `search_path` is set to another schema
- **THEN** `current_schema(conn)` returns that schema
