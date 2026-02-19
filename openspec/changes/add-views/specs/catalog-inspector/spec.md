## ADDED Requirements

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
