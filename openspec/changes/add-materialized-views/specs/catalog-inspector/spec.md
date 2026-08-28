## ADDED Requirements

### Requirement: MaterializedViewInfo type

The module SHALL provide a `MaterializedViewInfo` NamedTuple representing a single PostgreSQL materialized view as
loaded from the catalog.

#### Scenario: MaterializedViewInfo fields

- **WHEN** a `MaterializedViewInfo` instance is created
- **THEN** it has the following fields:
  - `schema` (`str`): the namespace name from `pg_namespace.nspname`
  - `name` (`str`): the relation name from `pg_class.relname`
  - `definition` (`str`): the complete reconstructed DDL in the form
    `CREATE MATERIALIZED VIEW <schema>.<name> AS\n<pg_get_viewdef()>`

#### Scenario: MaterializedViewInfo identity

- **WHEN** two `MaterializedViewInfo` instances have the same `schema` and `name`
- **THEN** they represent the same database materialized view
- **AND** `info[:-1]` yields that identity, consistent with the other catalog types

#### Scenario: Definition has no OR REPLACE and no data clause

- **WHEN** a materialized view `public.mv_sales` is inspected
- **THEN** its `definition` starts with `CREATE MATERIALIZED VIEW public.mv_sales AS`
- **AND** the definition contains no `OR REPLACE`, because PostgreSQL rejects that syntax for materialized views
- **AND** the definition contains no `WITH DATA` or `WITH NO DATA` clause, because `pg_get_viewdef()` returns only the
  query body

### Requirement: Bulk-load materialized view definitions from PostgreSQL catalog

The module SHALL provide an `inspect_materialized_views` function that queries `pg_class` (`relkind = 'm'`) joined with
`pg_namespace`. It SHALL use `pg_get_viewdef(oid, true)` for the canonical query body and reconstruct the full DDL with
`quote_ident()` for the schema and name. It SHALL exclude materialized views owned by a PostgreSQL extension by
filtering out rows where `pg_depend` contains a dependency with `deptype = 'e'` and
`classid = 'pg_catalog.pg_class'::regclass`. It SHALL return a sequence of `MaterializedViewInfo` instances.

#### Scenario: Load all materialized views from default schemas

- **WHEN** `inspect_materialized_views(conn)` is called without specifying schemas
- **THEN** it returns `MaterializedViewInfo` instances for all materialized views in schemas other than `pg_catalog` and
  `information_schema`

#### Scenario: Load materialized views from specific schemas

- **WHEN** `inspect_materialized_views(conn, schemas=["public", "reporting"])` is called
- **THEN** it returns instances only for materialized views in the `public` and `reporting` schemas

#### Scenario: Regular views are excluded

- **WHEN** the database contains both regular views (`relkind = 'v'`) and materialized views (`relkind = 'm'`)
- **THEN** `inspect_materialized_views` returns only the materialized views
- **AND** `inspect_views` continues to return only the regular views

#### Scenario: Population state does not affect the definition

- **WHEN** two materialized views have identical queries, one created `WITH DATA` and one created `WITH NO DATA`
- **THEN** their `definition` fields have identical query bodies
- **AND** `pg_class.relispopulated` is not reflected in the result

#### Scenario: Extension-owned materialized views are excluded

- **WHEN** an extension owns a materialized view in a managed schema, recorded in `pg_depend` with `deptype = 'e'`
- **THEN** `inspect_materialized_views` does not include it in the results

#### Scenario: Single query execution

- **WHEN** `inspect_materialized_views` is called
- **THEN** it executes exactly one SQL query, including the extension filter

#### Scenario: Empty result when no materialized views exist

- **WHEN** `inspect_materialized_views` is called on a database without materialized views
- **THEN** it returns an empty sequence

### Requirement: Load index DDL for a materialized view

The module SHALL provide an `inspect_matview_indexes` function that returns the complete `CREATE INDEX` DDL statements
for one materialized view. It SHALL query `pg_index` joined with `pg_class` and `pg_namespace` and use
`pg_get_indexdef(indexrelid)` for each index. Replacement needs these statements because `DROP MATERIALIZED VIEW`
removes the indexes silently and `REFRESH MATERIALIZED VIEW CONCURRENTLY` requires a unique index.

#### Scenario: Index DDL is complete and replayable

- **WHEN** `inspect_matview_indexes(conn, schema="public", name="mv_sales")` is called and the materialized view has a
  unique index on `(category)`
- **THEN** the result contains a statement of the form
  `CREATE UNIQUE INDEX mv_sales_category_idx ON public.mv_sales USING btree (category)`
- **AND** executing that statement against a freshly re-created `public.mv_sales` re-creates the index

#### Scenario: Deterministic ordering

- **WHEN** a materialized view has several indexes
- **THEN** the returned statements are ordered deterministically, sorted by their DDL text

#### Scenario: No indexes

- **WHEN** the materialized view has no indexes
- **THEN** the result is an empty sequence
