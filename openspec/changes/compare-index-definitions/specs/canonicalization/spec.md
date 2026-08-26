## ADDED Requirements

### Requirement: Canonicalize desired indexes through PostgreSQL

The module SHALL provide a `canonicalize_indexes(conn, *, schema, table_name, indexes)` function that normalizes
SQLAlchemy `Index` objects by creating them inside a rolled-back savepoint and reading back their deparsed form,
returning a mapping of index name to `IndexInfo`.

#### Scenario: Round-trip equality

- **WHEN** a metadata index matches an index already in the catalog
- **THEN** the returned `IndexInfo` has the same `unique` and `shape` as the one `inspect_indexes` reports for it

#### Scenario: Predicate rewrites are normalized

- **WHEN** a metadata index declares `postgresql_where=text("status IN ('a','b')")`
- **THEN** the returned shape contains PostgreSQL's own form, `WHERE (status = ANY (ARRAY['a'::text, 'b'::text]))`

#### Scenario: Result is comparable with the catalog

- **WHEN** the returned mapping is compared against `inspect_indexes` output for the same table
- **THEN** `schema` and `table_name` carry the caller's values, not the clone's, so entries compare directly

#### Scenario: Empty input

- **WHEN** `indexes` is empty
- **THEN** an empty mapping is returned and no DDL is executed

### Requirement: Probe on an empty temporary clone

The function SHALL create the probe indexes on a `TEMP` clone of the target table rather than on the target table
itself.

#### Scenario: The real table is not touched

- **WHEN** canonicalization runs against a table
- **THEN** every probe index is created on a relation in the connection's temporary schema
- **AND** no index is added to the real table, even transiently

#### Scenario: Clone reproduces the deparse context

- **WHEN** an index using expressions, operator classes, `INCLUDE`, `NULLS NOT DISTINCT`, or a `WHERE` predicate is
  probed
- **THEN** the shape read back from the clone equals the shape the same index produces on the real table

#### Scenario: Unqualified metadata resolves to the clone

- **WHEN** the metadata table leaves its schema implicit
- **THEN** the compiled `CREATE INDEX` resolves to the clone, because the clone takes the table's own name and `pg_temp`
  is searched first

#### Scenario: Schema-qualified metadata resolves to the clone

- **WHEN** the metadata table names its schema explicitly
- **THEN** the DDL is executed under a `schema_translate_map` redirecting that schema to `pg_temp`, so it still resolves
  to the clone

#### Scenario: Both spellings of the temporary schema are read back

- **WHEN** the server deparses the clone's table reference as `pg_temp.t` (PostgreSQL 15 and newer) or as the backing
  `pg_temp_3.t` (PostgreSQL 14)
- **THEN** the probed definition is stripped to the same shape either way

#### Scenario: An unrecognised rendering is not guessed at

- **WHEN** a probed definition matches neither spelling
- **THEN** a warning is logged and that index is absent from the result, rather than being returned with a half-stripped
  shape that could never match a catalog one

### Requirement: Savepoint leaves the database unchanged

The function SHALL roll back its savepoint, leaving neither probe indexes nor the clone behind.

#### Scenario: No trace after canonicalization

- **WHEN** canonicalization completes, whether or not every index succeeded
- **THEN** the set of indexes on the target table is unchanged
- **AND** no relation remains in the connection's temporary schema

### Requirement: Unusable indexes are omitted rather than raised

The function SHALL isolate each probe so that an index that cannot be created is omitted from the result while the
others still canonicalize.

#### Scenario: One unusable index among several

- **WHEN** one metadata index references a function or column that does not exist
- **THEN** a warning is logged, that name is absent from the returned mapping, and the remaining indexes are still
  returned

#### Scenario: The clone cannot be created

- **WHEN** creating the temporary clone fails
- **THEN** a warning is logged and an empty mapping is returned rather than an exception propagating
