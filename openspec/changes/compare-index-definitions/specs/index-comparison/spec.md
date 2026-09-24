## ADDED Requirements

### Requirement: Plugin registration

The package SHALL register the index comparator as a separate Alembic plugin named `alembic_pg_autogen.indexes`. This
plugin is separate from `alembic_pg_autogen.compare` and `alembic_pg_autogen.checkconstraints`.

#### Scenario: Registration at import

- **WHEN** `import alembic_pg_autogen` runs
- **THEN** the Alembic plugin registry contains a plugin named `alembic_pg_autogen.indexes`

#### Scenario: Registration as a table comparator for PostgreSQL that runs last

- **WHEN** `setup(plugin)` is called
- **THEN** it calls `plugin.add_autogenerate_comparator()` with `compare_target="table"`, the compare element
  `"index_definitions"`, `qualifier="postgresql"`, and `priority=DispatchPriority.LAST`

#### Scenario: The usual wildcard enables the plugin

- **WHEN** `context.configure(autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"])` is used
- **THEN** this plugin is enabled together with the other plugins

#### Scenario: Users can disable the plugin alone

- **WHEN** `autogenerate_plugins` contains `~alembic_pg_autogen.indexes`
- **THEN** index comparison does not run
- **AND** function, trigger, view, and check constraint comparison still runs

### Requirement: Compare the definitions of indexes in target metadata

For each table in both the database and `target_metadata`, the comparator SHALL compare the canonical definition of each
named index that has the same name on both sides. It SHALL NOT examine indexes that exist on one side only.

#### Scenario: New predicate

- **WHEN** the database has `CREATE INDEX ix_t_a ON t (a)` and the model declares
  `Index("ix_t_a", "a", postgresql_where=text("deleted_at IS NULL"))`
- **THEN** the comparator appends a `DropIndexOp` for the reflected index and a `CreateIndexOp` for the metadata index,
  in that order

#### Scenario: Removed predicate

- **WHEN** the database has a partial index and the model declares the same index with no `postgresql_where`
- **THEN** the comparator appends a drop/create pair

#### Scenario: Changed predicate

- **WHEN** the database has `WHERE deleted_at IS NULL` and the model declares `postgresql_where=text("status = 'x'")`
- **THEN** the comparator appends a drop/create pair

#### Scenario: Changed access method

- **WHEN** the database has a `btree` index and the model declares `postgresql_using="gin"`
- **THEN** the comparator appends a drop/create pair

#### Scenario: Changed operator class

- **WHEN** the database has `USING gin (data)` with the default `jsonb_ops` and the model declares
  `postgresql_ops={"data": "jsonb_path_ops"}`
- **THEN** the comparator appends a drop/create pair

#### Scenario: New INCLUDE columns

- **WHEN** the database has `CREATE INDEX ix_t_a ON t (a)` and the model declares `postgresql_include=["b"]`
- **THEN** the comparator appends a drop/create pair

#### Scenario: Expressions that differ only by a cast

- **WHEN** the database indexes `a` and the model declares `Index("ix", text("(a::int)"))`
- **THEN** the comparator appends a drop/create pair
- **AND** this happens although the Alembic expression rule removes the cast and reports the two as equal

#### Scenario: An equivalent definition gives no operation

- **WHEN** the model declares `postgresql_where=text("status IN ('x','y')")` and the catalog holds the same predicate as
  `(status = ANY (ARRAY['x'::text, 'y'::text]))`
- **THEN** the comparator emits no operation, because the round trip gives the same form for both

#### Scenario: A second autogenerate gives no operation

- **WHEN** a migration from this comparator is applied and autogenerate runs again against the same metadata
- **THEN** the comparator emits no index operation

#### Scenario: The downgrade restores the old definition

- **WHEN** the operations for a changed index are reversed for the downgrade
- **THEN** the downgrade drops the index and creates it again with the definition from the catalog

#### Scenario: The comparator skips tables that exist on one side only

- **WHEN** `conn_table` or `metadata_table` is `None`
- **THEN** the comparator returns `PriorityDispatchResult.CONTINUE` and emits no operation

#### Scenario: The comparator skips offline autogenerate

- **WHEN** `autogen_context.connection` is `None`
- **THEN** the comparator returns `PriorityDispatchResult.CONTINUE` and emits no operation

#### Scenario: The comparator returns CONTINUE

- **WHEN** the comparator completes, with or without operations
- **THEN** it returns `PriorityDispatchResult.CONTINUE`, so that the other table comparators still run

### Requirement: Skip indexes that Alembic already changed

The comparator SHALL skip each index that already has an operation in `modify_table_ops`. Thus one index never gets two
drop/create pairs.

#### Scenario: Alembic already found a change

- **WHEN** the Alembic comparator appended a drop/create pair for an index with a changed expression
- **THEN** this comparator emits no other operation for that index
- **AND** the migration contains exactly one drop/create pair for the index

#### Scenario: The index exists on one side only

- **WHEN** an index is declared in metadata but does not exist in the database
- **THEN** this comparator emits nothing for it, because Alembic owns existence

### Requirement: Index selection rules

The comparator SHALL examine only named indexes that are in `target_metadata` and on the reflected table.

#### Scenario: The comparator ignores unnamed indexes

- **WHEN** a metadata index has no name
- **THEN** the comparator ignores it, because it cannot match the index to a catalog index by name

#### Scenario: The comparator ignores indexes that implement constraints

- **WHEN** an index implements a primary key, unique, or exclusion constraint
- **THEN** the inspector does not return it, so the comparator never compares it

#### Scenario: The comparator applies filters

- **WHEN** `include_name` or `include_object` excludes an index with type `"index"`
- **THEN** the comparator emits no operation for it

### Requirement: A failure gives "unchanged"

The comparator SHALL report each index that it cannot normalize as unchanged. It logs a warning and does not raise.

#### Scenario: An index that does not apply

- **WHEN** the normalization test fails, for example because the expression uses a column that does not exist yet
- **THEN** a warning is logged and the comparator emits no operation for that index
- **AND** the comparator still compares the other indexes on the table

#### Scenario: The connection cannot create a temporary table

- **WHEN** the connection cannot create a temporary table
- **THEN** a warning is logged and the comparator reports each index on that table as unchanged

### Requirement: Option for concurrent rendering

When the autogenerate option `pg_index_concurrently` is true, the comparator SHALL emit operations that render inside an
autocommit block with `postgresql_concurrently=True`.

#### Scenario: The option is not set

- **WHEN** `pg_index_concurrently` is not set
- **THEN** the comparator emits the Alembic `DropIndexOp` and `CreateIndexOp`
- **AND** they render as ordinary `op.drop_index()` and `op.create_index()` calls

#### Scenario: The option is set

- **WHEN** `pg_index_concurrently=True` is passed to `context.configure()`
- **THEN** the comparator wraps each operation
- **AND** the rendered migration contains `with op.get_context().autocommit_block():` followed by the indented
  `op.create_index(...)` or `op.drop_index(...)` call with `postgresql_concurrently=True`

#### Scenario: The option does not change autogenerate

- **WHEN** the option is set
- **THEN** the canonicalization test still creates an ordinary index on the empty copy
