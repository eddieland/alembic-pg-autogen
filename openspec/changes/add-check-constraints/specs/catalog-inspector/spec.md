## ADDED Requirements

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
