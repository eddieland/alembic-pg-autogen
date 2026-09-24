## ADDED Requirements

### Requirement: IndexInfo type

The module SHALL provide an `IndexInfo` NamedTuple. It holds one PostgreSQL index as the catalog stores it.

#### Scenario: IndexInfo fields

- **WHEN** an `IndexInfo` instance is created
- **THEN** it has these fields:
  - `schema` (`str`): the namespace of the indexed table, from `pg_namespace.nspname`
  - `table_name` (`str`): the indexed table, from `pg_class.relname`
  - `name` (`str`): the index name, from `pg_class.relname` of the index relation
  - `unique` (`bool`): `pg_index.indisunique`
  - `shape` (`str`): the `pg_get_indexdef()` output from `USING` to the end

#### Scenario: IndexInfo identity

- **WHEN** two `IndexInfo` instances have the same `schema`, `table_name`, and `name`
- **THEN** they are the same database index

#### Scenario: Two fields hold the payload

- **WHEN** the inspector reads a unique index
- **THEN** `unique` is `True` and `shape` does not contain the word `UNIQUE`
- **AND** the reason is that `UNIQUE` is in the start of the statement, next to the identity, not in the shape

#### Scenario: The shape contains each PostgreSQL option

- **WHEN** the inspector reads an index declared as
  `CREATE INDEX ix ON t USING btree (a, lower(b)) INCLUDE (c) WHERE (d IS NULL)`
- **THEN** `shape` is `USING btree (a, lower(b)) INCLUDE (c) WHERE (d IS NULL)`
- **AND** it does not contain `CREATE INDEX`, the index name, or the table reference

### Requirement: Read all index definitions from the PostgreSQL catalog in one query

The module SHALL provide an `inspect_indexes` function. It queries `pg_index` joined with `pg_class` and `pg_namespace`.
It uses `pg_get_indexdef()` for the canonical definition. It returns a sequence of `IndexInfo` instances.

#### Scenario: Read all indexes from the default schemas

- **WHEN** `inspect_indexes(conn)` is called with no schemas
- **THEN** it returns the indexes in all schemas except `pg_catalog` and `information_schema`

#### Scenario: Read indexes from specified schemas

- **WHEN** `inspect_indexes(conn, schemas=["public"])` is called
- **THEN** it returns only the indexes on tables in `public`

#### Scenario: Read indexes of specified tables

- **WHEN** `inspect_indexes(conn, schemas=["public"], table_names=["t"])` is called
- **THEN** it returns only the indexes on `public.t`

#### Scenario: The result omits indexes that implement constraints

- **WHEN** a table has a primary key, a unique constraint, or an exclusion constraint
- **THEN** the result does not contain the indexes of those constraints, because the constraint owns the index

#### Scenario: The result omits indexes that an extension owns

- **WHEN** an index depends on an extension through `pg_depend.deptype = 'e'`
- **THEN** the result does not contain the index

#### Scenario: The query checks the identity prefix before it removes it

- **WHEN** an index named `ix USING y` exists on a table named `tbl USING x`
- **THEN** its `shape` starts with `USING btree`
- **AND** the reason is that the query builds the prefix with `quote_ident()` and compares it, and does not search for
  the first `USING`

#### Scenario: The result omits definitions with an unknown prefix

- **WHEN** the definition of an index does not start with the expected identity prefix
- **THEN** a warning is logged and the result does not contain the index
- **AND** the function does not return an unstripped shape, because such a shape can never match a canonical shape

#### Scenario: Empty result when no indexes exist

- **WHEN** `inspect_indexes` is called against a schema with no indexes
- **THEN** it returns an empty sequence

#### Scenario: One SQL query

- **WHEN** `inspect_indexes` is called
- **THEN** it runs exactly one SQL query
