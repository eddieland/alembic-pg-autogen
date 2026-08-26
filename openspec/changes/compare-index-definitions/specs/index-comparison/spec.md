## ADDED Requirements

### Requirement: Plugin registration

The package SHALL register the index comparator as its own Alembic plugin named `alembic_pg_autogen.indexes`, separate
from `alembic_pg_autogen.compare` and `alembic_pg_autogen.checkconstraints`.

#### Scenario: Registered at import time

- **WHEN** `import alembic_pg_autogen` runs
- **THEN** a plugin named `alembic_pg_autogen.indexes` is present in Alembic's plugin registry

#### Scenario: Registered as a table-level comparator for PostgreSQL, running last

- **WHEN** `setup(plugin)` is called
- **THEN** it calls `plugin.add_autogenerate_comparator()` with `compare_target="table"`, compare element
  `"index_definitions"`, `qualifier="postgresql"`, and `priority=DispatchPriority.LAST`

#### Scenario: Enabled by the usual wildcard

- **WHEN** `context.configure(autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"])` is used
- **THEN** this plugin is enabled alongside the others

#### Scenario: Independently disableable

- **WHEN** `~alembic_pg_autogen.indexes` is included in `autogenerate_plugins`
- **THEN** index comparison is skipped
- **AND** function, trigger, view, and check constraint comparison still runs

### Requirement: Compare definitions of indexes declared in target metadata

The comparator SHALL compare, for each table present in both the database and `target_metadata`, the canonical
definition of every named index whose name appears on both sides. It SHALL NOT consider indexes that exist on only one
side.

#### Scenario: Predicate added

- **WHEN** the database has `CREATE INDEX ix_t_a ON t (a)` and the model declares
  `Index("ix_t_a", "a", postgresql_where=text("deleted_at IS NULL"))`
- **THEN** a `DropIndexOp` for the reflected index and a `CreateIndexOp` for the metadata index are appended, in that
  order

#### Scenario: Predicate removed

- **WHEN** the database has a partial index and the model declares the same index with no `postgresql_where`
- **THEN** a drop/create pair is appended

#### Scenario: Predicate changed

- **WHEN** the database has `WHERE deleted_at IS NULL` and the model declares `postgresql_where=text("status = 'x'")`
- **THEN** a drop/create pair is appended

#### Scenario: Access method changed

- **WHEN** the database has a `btree` index and the model declares `postgresql_using="gin"`
- **THEN** a drop/create pair is appended

#### Scenario: Operator class changed

- **WHEN** the database has `USING gin (data)` with the default `jsonb_ops` and the model declares
  `postgresql_ops={"data": "jsonb_path_ops"}`
- **THEN** a drop/create pair is appended

#### Scenario: INCLUDE columns added

- **WHEN** the database has `CREATE INDEX ix_t_a ON t (a)` and the model declares `postgresql_include=["b"]`
- **THEN** a drop/create pair is appended

#### Scenario: Expression differing only by a cast

- **WHEN** the database indexes `a` and the model declares `Index("ix", text("(a::int)"))`
- **THEN** a drop/create pair is appended, even though Alembic's expression heuristic strips the cast and reports the
  two as equal

#### Scenario: Equivalent definition produces nothing

- **WHEN** the model declares `postgresql_where=text("status IN ('x','y')")` and the catalog holds the same predicate as
  `(status = ANY (ARRAY['x'::text, 'y'::text]))`
- **THEN** no operation is emitted, because the round-trip normalizes both to the same form

#### Scenario: Autogenerate converges

- **WHEN** a migration emitted by this comparator has been applied and autogenerate is run again against the same
  metadata
- **THEN** no further index operation is emitted

#### Scenario: Downgrade restores the previous definition

- **WHEN** a changed index's operations are reversed for the downgrade
- **THEN** the downgrade drops the index and recreates it with the definition read from the catalog

#### Scenario: Tables on only one side are skipped

- **WHEN** either `conn_table` or `metadata_table` is `None`
- **THEN** the comparator returns `PriorityDispatchResult.CONTINUE` without emitting operations

#### Scenario: Offline autogenerate is skipped

- **WHEN** `autogen_context.connection` is `None`
- **THEN** the comparator returns `PriorityDispatchResult.CONTINUE` without emitting operations

#### Scenario: Comparator returns CONTINUE

- **WHEN** the comparator finishes, whether or not it emitted operations
- **THEN** it returns `PriorityDispatchResult.CONTINUE` so other table-level comparators still run

### Requirement: Defer to Alembic on indexes it already handled

The comparator SHALL skip any index for which an operation is already present in `modify_table_ops`, so that a single
index never draws two drop/create pairs.

#### Scenario: Alembic already detected a change

- **WHEN** Alembic's own comparator has appended a drop/create pair for an index whose expression changed
- **THEN** this comparator emits nothing further for that index
- **AND** the migration contains exactly one drop/create pair for it

#### Scenario: Index exists on only one side

- **WHEN** an index is declared in metadata but absent from the database
- **THEN** this comparator emits nothing for it, because Alembic owns existence

### Requirement: Index selection rules

The comparator SHALL consider only named indexes present in `target_metadata` and reflected on the connection table.

#### Scenario: Unnamed indexes are ignored

- **WHEN** a metadata index has no name
- **THEN** it is ignored, because it cannot be matched to a catalog index by name

#### Scenario: Constraint-backed indexes are ignored

- **WHEN** an index implements a primary key, unique, or exclusion constraint
- **THEN** it is not reported by the inspector and so is never compared

#### Scenario: Filters are honored

- **WHEN** `include_name` or `include_object` excludes an index with type `"index"`
- **THEN** no operation is emitted for it

### Requirement: Failure degrades to "unchanged"

The comparator SHALL treat any index it cannot normalize as unchanged, logging a warning rather than raising.

#### Scenario: Index that will not apply

- **WHEN** the normalization probe fails, for example because the expression references a column that does not exist yet
- **THEN** a warning is logged and no operation is emitted for that index
- **AND** other indexes on the table are still compared

#### Scenario: Temporary table creation is unavailable

- **WHEN** the connection cannot create a temporary table
- **THEN** a warning is logged and every index on that table is reported unchanged

### Requirement: Concurrent rendering opt-in

The comparator SHALL, when the `pg_index_concurrently` autogenerate option is true, emit operations that render inside
an autocommit block with `postgresql_concurrently=True`.

#### Scenario: Option absent

- **WHEN** `pg_index_concurrently` is not set
- **THEN** Alembic's own `DropIndexOp` and `CreateIndexOp` are emitted and render as ordinary `op.drop_index()` /
  `op.create_index()` calls

#### Scenario: Option enabled

- **WHEN** `pg_index_concurrently=True` is passed to `context.configure()`
- **THEN** each operation is wrapped so that the rendered migration reads `with op.get_context().autocommit_block():`
  followed by the indented `op.create_index(...)` or `op.drop_index(...)` call carrying `postgresql_concurrently=True`

#### Scenario: Concurrency does not affect autogenerate itself

- **WHEN** the option is enabled
- **THEN** the canonicalization probe still creates an ordinary index on the throwaway clone
