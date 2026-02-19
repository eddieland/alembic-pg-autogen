## ADDED Requirements

### Requirement: CreateViewOp type

The module SHALL provide a `CreateViewOp` class extending `MigrateOperation` that represents creating a new PostgreSQL
view.

#### Scenario: CreateViewOp fields

- **WHEN** a `CreateViewOp` is constructed from a `ViewOp` with `action=Action.CREATE`
- **THEN** it stores the `desired` `ViewInfo` (which contains the full `CREATE OR REPLACE VIEW` DDL in its `definition`
  field)

#### Scenario: CreateViewOp reverse

- **WHEN** `reverse()` is called on a `CreateViewOp`
- **THEN** it returns a `DropViewOp` referencing the same `ViewInfo`

#### Scenario: CreateViewOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called on a `CreateViewOp` with `desired.schema="public"`, `desired.name="active_users"`
- **THEN** it returns `("create_view", "public", "active_users")`

### Requirement: ReplaceViewOp type

The module SHALL provide a `ReplaceViewOp` class extending `MigrateOperation` that represents replacing an existing
PostgreSQL view with a new definition.

#### Scenario: ReplaceViewOp fields

- **WHEN** a `ReplaceViewOp` is constructed from a `ViewOp` with `action=Action.REPLACE`
- **THEN** it stores both the `current` and `desired` `ViewInfo` instances

#### Scenario: ReplaceViewOp reverse

- **WHEN** `reverse()` is called on a `ReplaceViewOp` with `current=A` and `desired=B`
- **THEN** it returns a `ReplaceViewOp` with `current=B` and `desired=A`

#### Scenario: ReplaceViewOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called on a `ReplaceViewOp` with `desired.schema="reporting"`,
  `desired.name="monthly_summary"`
- **THEN** it returns `("replace_view", "reporting", "monthly_summary")`

### Requirement: DropViewOp type

The module SHALL provide a `DropViewOp` class extending `MigrateOperation` that represents dropping an existing
PostgreSQL view.

#### Scenario: DropViewOp fields

- **WHEN** a `DropViewOp` is constructed from a `ViewOp` with `action=Action.DROP`
- **THEN** it stores the `current` `ViewInfo` (needed to reconstruct the view on downgrade)

#### Scenario: DropViewOp reverse

- **WHEN** `reverse()` is called on a `DropViewOp`
- **THEN** it returns a `CreateViewOp` referencing the same `ViewInfo`

#### Scenario: DropViewOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called on a `DropViewOp` with `current.schema="public"`, `current.name="old_view"`
- **THEN** it returns `("drop_view", "public", "old_view")`

### Requirement: All view operation classes extend MigrateOperation

All three view operation classes SHALL extend `alembic.operations.ops.MigrateOperation`. They SHALL NOT register
themselves via `@Operations.register_operation` or `@Operations.implementation_for` — they are autogenerate-time
constructs only.

#### Scenario: Inheritance

- **WHEN** any of the three view op classes is inspected
- **THEN** it is a subclass of `alembic.operations.ops.MigrateOperation`

#### Scenario: No operation registration

- **WHEN** `_ops.py` is imported
- **THEN** no view-related methods are added to `alembic.operations.base.Operations`

## MODIFIED Requirements

### Requirement: Public exports

The module SHALL export all nine operation classes (six function/trigger + three view) as public API via the package's
`__init__.py` and `__all__`.

#### Scenario: All types importable from package root

- **WHEN** a user writes
  `from alembic_pg_autogen import CreateFunctionOp, ReplaceFunctionOp, DropFunctionOp, CreateTriggerOp, ReplaceTriggerOp, DropTriggerOp, CreateViewOp, ReplaceViewOp, DropViewOp`
- **THEN** the import succeeds

#### Scenario: Listed in \_\_all\_\_

- **WHEN** `alembic_pg_autogen.__all__` is inspected
- **THEN** it contains all nine operation class names
