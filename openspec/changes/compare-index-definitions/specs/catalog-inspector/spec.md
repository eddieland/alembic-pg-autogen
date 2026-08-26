## ADDED Requirements

### Requirement: IndexInfo type

The module SHALL provide an `IndexInfo` NamedTuple representing a single PostgreSQL index as loaded from the catalog.

#### Scenario: IndexInfo fields

- **WHEN** an `IndexInfo` instance is created
- **THEN** it has the following fields:
  - `schema` (`str`): the indexed table's namespace from `pg_namespace.nspname`
  - `table_name` (`str`): the indexed table from `pg_class.relname`
  - `name` (`str`): the index name from `pg_class.relname` of the index relation
  - `unique` (`bool`): `pg_index.indisunique`
  - `shape` (`str`): everything `pg_get_indexdef()` emits from `USING` onward

#### Scenario: IndexInfo identity

- **WHEN** two `IndexInfo` instances have the same `schema`, `table_name`, and `name`
- **THEN** they represent the same database index

#### Scenario: Payload is split across two fields

- **WHEN** a unique index is inspected
- **THEN** `unique` is `True` and `shape` does not contain the word `UNIQUE`, because `UNIQUE` appears in the statement
  head alongside the identity rather than in the shape

#### Scenario: Shape covers every PostgreSQL-specific feature

- **WHEN** an index declared as `CREATE INDEX ix ON t USING btree (a, lower(b)) INCLUDE (c) WHERE (d IS NULL)` is
  inspected
- **THEN** `shape` is `USING btree (a, lower(b)) INCLUDE (c) WHERE (d IS NULL)`
- **AND** it excludes the `CREATE INDEX`, the index name, and the table reference

### Requirement: Bulk-load index definitions from PostgreSQL catalog

The module SHALL provide an `inspect_indexes` function that queries `pg_index` joined with `pg_class` and
`pg_namespace`, using `pg_get_indexdef()` for the canonical definition, returning a sequence of `IndexInfo` instances.

#### Scenario: Load all indexes from default schemas

- **WHEN** `inspect_indexes(conn)` is called without specifying schemas
- **THEN** it returns entries for indexes in schemas other than `pg_catalog` and `information_schema`

#### Scenario: Load indexes from specific schemas

- **WHEN** `inspect_indexes(conn, schemas=["public"])` is called
- **THEN** only indexes on tables in `public` are returned

#### Scenario: Restrict to specific tables

- **WHEN** `inspect_indexes(conn, schemas=["public"], table_names=["t"])` is called
- **THEN** only indexes on `public.t` are returned

#### Scenario: Constraint-backed indexes are excluded

- **WHEN** a table has a primary key, a unique constraint, or an exclusion constraint
- **THEN** the indexes implementing them do not appear in the results, because the constraint owns the index

#### Scenario: Extension-owned indexes are excluded

- **WHEN** an index depends on an extension with `pg_depend.deptype = 'e'`
- **THEN** it is excluded from the results

#### Scenario: Identity prefix stripping is verified, not assumed

- **WHEN** an index named `ix USING y` exists on a table named `tbl USING x`
- **THEN** its `shape` still begins with `USING btree`, because the prefix is rebuilt with `quote_ident()` and matched
  rather than found by searching for the first occurrence of `USING`

#### Scenario: Unstrippable definitions are omitted

- **WHEN** an index's definition does not begin with the expected identity prefix
- **THEN** a warning is logged and the index is omitted from the results, rather than returned with an unstripped shape
  that could never match a canonicalized one

#### Scenario: Empty result when no indexes exist

- **WHEN** `inspect_indexes` is called against a schema with no indexes
- **THEN** it returns an empty sequence

#### Scenario: Single query execution

- **WHEN** `inspect_indexes` is called
- **THEN** it executes exactly one SQL query
