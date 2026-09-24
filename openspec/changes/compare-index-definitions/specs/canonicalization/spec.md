## ADDED Requirements

### Requirement: Canonicalize desired indexes through PostgreSQL

The module SHALL provide a `canonicalize_indexes(conn, *, schema, table_name, indexes)` function. The function creates
SQLAlchemy `Index` objects inside a savepoint and reads back their deparsed form. Then it rolls back the savepoint. It
returns a mapping of index name to `IndexInfo`.

#### Scenario: Round trip equality

- **WHEN** a metadata index matches an index in the catalog
- **THEN** the returned `IndexInfo` has the same `unique` and `shape` as the `inspect_indexes` result for that index

#### Scenario: Predicates are normalized

- **WHEN** a metadata index declares `postgresql_where=text("status IN ('a','b')")`
- **THEN** the returned shape contains the PostgreSQL form, `WHERE (status = ANY (ARRAY['a'::text, 'b'::text]))`

#### Scenario: The result compares directly with the catalog

- **WHEN** the returned mapping is compared with the `inspect_indexes` output for the same table
- **THEN** `schema` and `table_name` hold the values of the caller, not the values of the copy

#### Scenario: Empty input

- **WHEN** `indexes` is empty
- **THEN** the function returns an empty mapping and runs no DDL

### Requirement: Test on an empty temporary copy

The function SHALL create the test indexes on a `TEMP` copy of the target table, not on the target table.

#### Scenario: The real table does not change

- **WHEN** canonicalization runs against a table
- **THEN** each test index is created on a relation in the temporary schema of the connection
- **AND** no index is added to the real table, not even for a short time

#### Scenario: The copy reproduces the deparse context

- **WHEN** the function tests an index with expressions, operator classes, `INCLUDE`, `NULLS NOT DISTINCT`, or a `WHERE`
  predicate
- **THEN** the shape from the copy equals the shape that the same index gives on the real table

#### Scenario: Metadata with no explicit schema finds the copy

- **WHEN** the metadata table has no explicit schema
- **THEN** the compiled `CREATE INDEX` finds the copy, because the copy has the name of the table and PostgreSQL
  searches `pg_temp` first

#### Scenario: Metadata with an explicit schema finds the copy

- **WHEN** the metadata table has an explicit schema
- **THEN** the DDL runs under a `schema_translate_map` that changes that schema to `pg_temp`
- **AND** the DDL finds the copy

#### Scenario: The query reads both spellings of the temporary schema

- **WHEN** the server writes the table of the copy as `pg_temp.t` (PostgreSQL 15 and later) or as `pg_temp_3.t`
  (PostgreSQL 14)
- **THEN** the function removes the prefix and gets the same shape in both cases

#### Scenario: An unknown spelling is not guessed

- **WHEN** a test definition matches neither spelling
- **THEN** a warning is logged and the result does not contain that index
- **AND** the function does not return a partly stripped shape, because such a shape can never match a catalog shape

### Requirement: The savepoint leaves the database unchanged

The function SHALL roll back its savepoint. No test index and no copy remain after the function returns.

#### Scenario: No trace after canonicalization

- **WHEN** canonicalization completes, with or without failed indexes
- **THEN** the set of indexes on the target table does not change
- **AND** no relation remains in the temporary schema of the connection

### Requirement: The function omits bad indexes and does not raise

The function SHALL test each index separately. It omits an index that it cannot create from the result. It still
canonicalizes the other indexes.

#### Scenario: An index that SQLAlchemy cannot compile

- **WHEN** SQLAlchemy cannot render a metadata index to DDL, for example an expression whose type has no literal
  renderer
- **THEN** a warning is logged and the returned mapping does not contain that name
- **AND** the mapping contains the other indexes, because the function catches the compilation error together with the
  server errors

#### Scenario: A temporary relation already has the name of the copy

- **WHEN** a temporary relation on the connection already has the name of the target table
- **THEN** a warning that names this cause is logged and the function returns an empty mapping
- **AND** the existing temporary relation does not change

#### Scenario: One bad index among several

- **WHEN** one metadata index uses a function or column that does not exist
- **THEN** a warning is logged and the returned mapping does not contain that name
- **AND** the mapping contains the other indexes

#### Scenario: The copy cannot be created

- **WHEN** the creation of the temporary copy fails
- **THEN** a warning is logged and the function returns an empty mapping
- **AND** no exception propagates
