## ADDED Requirements

### Requirement: CreateMaterializedViewOp type

The module SHALL provide a `CreateMaterializedViewOp` class extending `MigrateOperation` that represents creating a new
PostgreSQL materialized view.

#### Scenario: CreateMaterializedViewOp fields

- **WHEN** a `CreateMaterializedViewOp` is constructed from a `MaterializedViewOp` with `action=Action.CREATE`
- **THEN** it stores the `desired` `MaterializedViewInfo`, whose `definition` field holds the full
  `CREATE MATERIALIZED VIEW` DDL

#### Scenario: CreateMaterializedViewOp reverse

- **WHEN** `reverse()` is called on a `CreateMaterializedViewOp`
- **THEN** it returns a `DropMaterializedViewOp` referencing the same `MaterializedViewInfo`

#### Scenario: CreateMaterializedViewOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called with `desired.schema="public"`, `desired.name="mv_sales"`
- **THEN** it returns `("create_materialized_view", "public", "mv_sales")`

### Requirement: ReplaceMaterializedViewOp type

The module SHALL provide a `ReplaceMaterializedViewOp` class extending `MigrateOperation` that represents replacing an
existing PostgreSQL materialized view. PostgreSQL has no `CREATE OR REPLACE MATERIALIZED VIEW` and no
`ALTER MATERIALIZED VIEW ... AS`, so replacement means drop and re-create. Because `DROP MATERIALIZED VIEW` removes
indexes silently, the operation SHALL also carry the index DDL of the object being replaced.

#### Scenario: ReplaceMaterializedViewOp fields

- **WHEN** a `ReplaceMaterializedViewOp` is constructed
- **THEN** it stores the `current` and `desired` `MaterializedViewInfo` instances
- **AND** an `index_ddl` sequence of `CREATE INDEX` statements read from the current object via `pg_get_indexdef()`

#### Scenario: ReplaceMaterializedViewOp reverse

- **WHEN** `reverse()` is called on a `ReplaceMaterializedViewOp` with `current=A`, `desired=B`, and `index_ddl=I`
- **THEN** it returns a `ReplaceMaterializedViewOp` with `current=B`, `desired=A`, and `index_ddl=I`
- **AND** the downgrade re-creates the indexes of the original object, because `I` was read from it

#### Scenario: ReplaceMaterializedViewOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called with `desired.schema="reporting"`, `desired.name="mv_totals"`
- **THEN** it returns `("replace_materialized_view", "reporting", "mv_totals")`

### Requirement: DropMaterializedViewOp type

The module SHALL provide a `DropMaterializedViewOp` class extending `MigrateOperation` that represents dropping an
existing PostgreSQL materialized view.

#### Scenario: DropMaterializedViewOp fields

- **WHEN** a `DropMaterializedViewOp` is constructed from a `MaterializedViewOp` with `action=Action.DROP`
- **THEN** it stores the `current` `MaterializedViewInfo`, needed to reconstruct the object on downgrade

#### Scenario: DropMaterializedViewOp reverse

- **WHEN** `reverse()` is called on a `DropMaterializedViewOp`
- **THEN** it returns a `CreateMaterializedViewOp` referencing the same `MaterializedViewInfo`
- **AND** the downgrade does not restore indexes, a documented limitation

#### Scenario: DropMaterializedViewOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called with `current.schema="public"`, `current.name="old_mv"`
- **THEN** it returns `("drop_materialized_view", "public", "old_mv")`
