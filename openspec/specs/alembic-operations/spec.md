## ADDED Requirements

### Requirement: CreateFunctionOp type

The module SHALL provide a `CreateFunctionOp` class extending `MigrateOperation` that represents creating a new
PostgreSQL function.

#### Scenario: CreateFunctionOp fields

- **WHEN** a `CreateFunctionOp` is constructed from a `FunctionOp` with `action=Action.CREATE`
- **THEN** it stores the `desired` `FunctionInfo` (which contains the full `CREATE OR REPLACE FUNCTION` DDL in its
  `definition` field)

#### Scenario: CreateFunctionOp reverse

- **WHEN** `reverse()` is called on a `CreateFunctionOp`
- **THEN** it returns a `DropFunctionOp` referencing the same `FunctionInfo`

#### Scenario: CreateFunctionOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called on a `CreateFunctionOp` with `desired.schema="public"`, `desired.name="my_fn"`,
  `desired.identity_args="integer"`
- **THEN** it returns `("create_function", "public", "my_fn", "integer")`

### Requirement: ReplaceFunctionOp type

The module SHALL provide a `ReplaceFunctionOp` class extending `MigrateOperation` that represents replacing an existing
PostgreSQL function with a new definition.

#### Scenario: ReplaceFunctionOp fields

- **WHEN** a `ReplaceFunctionOp` is constructed from a `FunctionOp` with `action=Action.REPLACE`
- **THEN** it stores both the `current` and `desired` `FunctionInfo` instances

#### Scenario: ReplaceFunctionOp reverse

- **WHEN** `reverse()` is called on a `ReplaceFunctionOp` with `current=A` and `desired=B`
- **THEN** it returns a `ReplaceFunctionOp` with `current=B` and `desired=A`

#### Scenario: ReplaceFunctionOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called on a `ReplaceFunctionOp` with `desired.schema="audit"`,
  `desired.name="log_change"`, `desired.identity_args=""`
- **THEN** it returns `("replace_function", "audit", "log_change", "")`

### Requirement: DropFunctionOp type

The module SHALL provide a `DropFunctionOp` class extending `MigrateOperation` that represents dropping an existing
PostgreSQL function.

#### Scenario: DropFunctionOp fields

- **WHEN** a `DropFunctionOp` is constructed from a `FunctionOp` with `action=Action.DROP`
- **THEN** it stores the `current` `FunctionInfo` (needed to reconstruct the function on downgrade)

#### Scenario: DropFunctionOp reverse

- **WHEN** `reverse()` is called on a `DropFunctionOp`
- **THEN** it returns a `CreateFunctionOp` referencing the same `FunctionInfo`

#### Scenario: DropFunctionOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called on a `DropFunctionOp` with `current.schema="public"`, `current.name="old_fn"`,
  `current.identity_args="text"`
- **THEN** it returns `("drop_function", "public", "old_fn", "text")`

### Requirement: CreateTriggerOp type

The module SHALL provide a `CreateTriggerOp` class extending `MigrateOperation` that represents creating a new
PostgreSQL trigger.

#### Scenario: CreateTriggerOp fields

- **WHEN** a `CreateTriggerOp` is constructed from a `TriggerOp` with `action=Action.CREATE`
- **THEN** it stores the `desired` `TriggerInfo` (which contains the full `CREATE TRIGGER` DDL in its `definition`
  field)

#### Scenario: CreateTriggerOp reverse

- **WHEN** `reverse()` is called on a `CreateTriggerOp`
- **THEN** it returns a `DropTriggerOp` referencing the same `TriggerInfo`

#### Scenario: CreateTriggerOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called on a `CreateTriggerOp` with `desired.schema="public"`,
  `desired.table_name="orders"`, `desired.trigger_name="audit_trg"`
- **THEN** it returns `("create_trigger", "public", "orders", "audit_trg")`

### Requirement: ReplaceTriggerOp type

The module SHALL provide a `ReplaceTriggerOp` class extending `MigrateOperation` that represents replacing an existing
PostgreSQL trigger with a new definition.

#### Scenario: ReplaceTriggerOp fields

- **WHEN** a `ReplaceTriggerOp` is constructed from a `TriggerOp` with `action=Action.REPLACE`
- **THEN** it stores both the `current` and `desired` `TriggerInfo` instances

#### Scenario: ReplaceTriggerOp reverse

- **WHEN** `reverse()` is called on a `ReplaceTriggerOp` with `current=A` and `desired=B`
- **THEN** it returns a `ReplaceTriggerOp` with `current=B` and `desired=A`

#### Scenario: ReplaceTriggerOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called on a `ReplaceTriggerOp` with `desired.schema="public"`,
  `desired.table_name="events"`, `desired.trigger_name="notify_trg"`
- **THEN** it returns `("replace_trigger", "public", "events", "notify_trg")`

### Requirement: DropTriggerOp type

The module SHALL provide a `DropTriggerOp` class extending `MigrateOperation` that represents dropping an existing
PostgreSQL trigger.

#### Scenario: DropTriggerOp fields

- **WHEN** a `DropTriggerOp` is constructed from a `TriggerOp` with `action=Action.DROP`
- **THEN** it stores the `current` `TriggerInfo` (needed to reconstruct the trigger on downgrade)

#### Scenario: DropTriggerOp reverse

- **WHEN** `reverse()` is called on a `DropTriggerOp`
- **THEN** it returns a `CreateTriggerOp` referencing the same `TriggerInfo`

#### Scenario: DropTriggerOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called on a `DropTriggerOp` with `current.schema="public"`,
  `current.table_name="users"`, `current.trigger_name="old_trg"`
- **THEN** it returns `("drop_trigger", "public", "users", "old_trg")`

### Requirement: All operation classes extend MigrateOperation

All six operation classes SHALL extend `alembic.operations.ops.MigrateOperation`. They SHALL NOT register themselves via
`@Operations.register_operation` or `@Operations.implementation_for` — they are autogenerate-time constructs only.

#### Scenario: Inheritance

- **WHEN** any of the six op classes is inspected
- **THEN** it is a subclass of `alembic.operations.ops.MigrateOperation`

#### Scenario: No operation registration

- **WHEN** `_ops.py` is imported
- **THEN** no methods are added to `alembic.operations.base.Operations` (no `op.create_function()` etc.)

### Requirement: Public exports

The module SHALL export all six operation classes as public API via the package's `__init__.py` and `__all__`.

#### Scenario: All types importable from package root

- **WHEN** a user writes
  `from alembic_pg_autogen import CreateFunctionOp, ReplaceFunctionOp, DropFunctionOp, CreateTriggerOp, ReplaceTriggerOp, DropTriggerOp`
- **THEN** the import succeeds

#### Scenario: Listed in \_\_all\_\_

- **WHEN** `alembic_pg_autogen.__all__` is inspected
- **THEN** it contains all six operation class names
