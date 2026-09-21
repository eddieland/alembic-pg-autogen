## ADDED Requirements

### Requirement: Skip `drop_index` for dropped tables

The package SHALL provide `skip_drop_index_for_dropped_tables` in an `alembic_pg_autogen.rewriters` module, exported
from the package root. It SHALL be an instance of `alembic.autogenerate.rewriter.Rewriter`, so that it is accepted as
the `process_revision_directives` argument of `context.configure()` and composes through `Rewriter.chain()`.

For each `UpgradeOps` and `DowngradeOps` container of a `MigrationScript`, the rewriter SHALL collect the
`(table_name, schema)` pairs of every `DropTableOp` in that container. It SHALL then remove every `DropIndexOp` whose
`(table_name, schema)` pair is in that set, whether the operation sits at the top level of the container or inside a
`ModifyTableOps` for the same table. A `ModifyTableOps` that becomes empty SHALL be removed. Every other operation SHALL
stay in place and in order.

#### Scenario: Dropped table with an index

- **WHEN** autogenerate produces `ModifyTableOps("orders", [DropIndexOp("ix_orders_customer", table_name="orders")])`
  followed by `DropTableOp("orders")` in `upgrade()`
- **THEN** after the rewriter runs, `upgrade()` contains only `DropTableOp("orders")`
- **AND** the rendered migration contains `op.drop_table("orders")` and no `op.drop_index(...)`

#### Scenario: Created table with an index

- **WHEN** autogenerate produces `CreateTableOp` and `CreateIndexOp` in `upgrade()` and the reversed `DropIndexOp` and
  `DropTableOp` in `downgrade()`
- **THEN** `upgrade()` is unchanged
- **AND** `downgrade()` contains only `DropTableOp`

#### Scenario: Index on a surviving table

- **WHEN** a `DropIndexOp` names a table that no `DropTableOp` in the same container drops
- **THEN** the `DropIndexOp` stays

#### Scenario: Same table name in another schema

- **WHEN** `DropIndexOp(..., table_name="orders", schema="archive")` and `DropTableOp("orders")` appear in the same
  container
- **THEN** the `DropIndexOp` stays, because the schemas differ

#### Scenario: Container with other operations

- **WHEN** the `ModifyTableOps` for a dropped table also holds an operation that is not a `DropIndexOp`
- **THEN** only the `DropIndexOp` is removed and the container stays with its remaining operations

#### Scenario: Multi-database script

- **WHEN** a `MigrationScript` carries several `UpgradeOps` in `upgrade_ops_list`
- **THEN** each container is rewritten independently

#### Scenario: Composition with an existing hook

- **WHEN** the user passes `skip_drop_index_for_dropped_tables.chain(my_hook)` or
  `my_writer.chain( skip_drop_index_for_dropped_tables)` to `context.configure()`
- **THEN** both hooks run, in the order given
- **AND** the module-level `skip_drop_index_for_dropped_tables` instance is unchanged

#### Scenario: Not configured

- **WHEN** the user does not pass the rewriter to `context.configure()`
- **THEN** autogenerate emits the `drop_index` operations exactly as Alembic does without this package
