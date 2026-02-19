## ADDED Requirements

### Requirement: Plugin setup function

The module SHALL provide a `setup(plugin: Plugin)` function that registers the comparator with Alembic's plugin system.
It SHALL register at the `"schema"` dispatch level with compare element `"pg_objects"`.

#### Scenario: Entry point registration

- **WHEN** the package is installed and `pyproject.toml` declares the entry point
  `[project.entry-points."alembic.plugins"] alembic_pg_autogen = "alembic_pg_autogen._compare"`
- **THEN** Alembic discovers and calls `setup()` automatically at import time

#### Scenario: Setup registers a schema-level comparator

- **WHEN** `setup(plugin)` is called with an Alembic `Plugin` instance
- **THEN** it calls `plugin.add_autogenerate_comparator()` with `compare_target="schema"` and
  `compare_element="pg_objects"`

### Requirement: Desired-state configuration keys

The comparator SHALL read desired function and trigger DDL from `autogen_context.opts` using the keys `pg_functions` and
`pg_triggers`. Both keys SHALL default to empty sequences if absent.

#### Scenario: Functions and triggers provided

- **WHEN** `context.configure()` is called with `pg_functions=["CREATE OR REPLACE FUNCTION ..."]` and
  `pg_triggers=["CREATE TRIGGER ..."]`
- **THEN** the comparator reads these sequences from `autogen_context.opts["pg_functions"]` and
  `autogen_context.opts["pg_triggers"]`

#### Scenario: Keys absent

- **WHEN** `context.configure()` is called without `pg_functions` or `pg_triggers`
- **THEN** the comparator treats both as empty sequences and produces no operations

#### Scenario: Only functions provided

- **WHEN** `context.configure()` is called with `pg_functions=[...]` but no `pg_triggers`
- **THEN** the comparator processes functions normally and treats triggers as an empty desired set
- **AND** existing database triggers in managed schemas are NOT dropped (only explicitly declared triggers are managed)

### Requirement: Comparator pipeline orchestration

The comparator SHALL execute the full inspect-canonicalize-diff pipeline when desired-state DDL is provided.

#### Scenario: Full pipeline execution

- **WHEN** the comparator fires with non-empty `pg_functions` and `pg_triggers`
- **THEN** it executes these steps in order:
  1. Inspect current functions and triggers from the database via `inspect_functions` and `inspect_triggers`
  1. Construct a `CanonicalState` from the inspection results
  1. Canonicalize the desired DDL via `canonicalize(conn, pg_functions, pg_triggers)`
  1. Diff the current state against the desired state via `diff(current, desired)`
  1. Map each `FunctionOp`/`TriggerOp` to the corresponding `MigrateOperation` subclass
  1. Append the operations to `upgrade_ops.ops`

#### Scenario: No changes detected

- **WHEN** the current database state matches the desired state exactly
- **THEN** the comparator appends no operations to `upgrade_ops.ops`

#### Scenario: Comparator returns CONTINUE

- **WHEN** the comparator finishes (whether or not it emitted ops)
- **THEN** it returns `PriorityDispatchResult.CONTINUE` to allow other schema-level comparators to run

### Requirement: Dependency-safe operation ordering

The comparator SHALL emit operations to `upgrade_ops.ops` in an order that respects dependencies between functions and
triggers. Triggers may reference functions, so function creation/replacement SHALL precede trigger creation/replacement,
and trigger drops SHALL precede function drops.

#### Scenario: Upgrade ordering

- **WHEN** the diff produces both function and trigger operations
- **THEN** operations are appended to `upgrade_ops.ops` in this order:
  1. `DropTriggerOp` instances (frees functions for removal)
  1. `DropFunctionOp` instances
  1. `CreateFunctionOp` and `ReplaceFunctionOp` instances (must exist before triggers reference them)
  1. `CreateTriggerOp` and `ReplaceTriggerOp` instances

#### Scenario: Downgrade ordering via reverse

- **WHEN** Alembic calls `upgrade_ops.reverse_into(downgrade_ops)`
- **THEN** the reversed order produces a valid downgrade sequence (drop triggers → drop functions → create/replace
  functions → create/replace triggers)

#### Scenario: Only function ops

- **WHEN** the diff produces only function operations (no trigger changes)
- **THEN** function ops are emitted directly without empty trigger op groups

### Requirement: Schema filtering

The comparator SHALL use the `schemas` parameter provided by Alembic's dispatch to filter which database objects are
inspected and which desired-state objects are included in the diff.

#### Scenario: Default schema only

- **WHEN** Alembic dispatches with `schemas={None}` (the default, representing the connection's default schema)
- **THEN** the comparator inspects only the default schema and filters canonicalized desired state to that schema

#### Scenario: Multiple schemas

- **WHEN** Alembic dispatches with `schemas={None, "audit", "reporting"}`
- **THEN** the comparator inspects functions and triggers in all three schemas
- **AND** canonicalized desired-state objects outside these schemas are excluded from the diff

### Requirement: Connection usage

The comparator SHALL use `autogen_context.connection` for all database operations. It SHALL NOT create connections,
engines, or manage top-level transactions.

#### Scenario: Uses autogenerate connection

- **WHEN** the comparator runs during `alembic revision --autogenerate`
- **THEN** it uses `autogen_context.connection` for `inspect_functions`, `inspect_triggers`, and `canonicalize`

### Requirement: Public exports

The module SHALL export the `setup` function as public API. The `setup` function SHALL be listed in the package's
`__all__`.

#### Scenario: setup importable from package root

- **WHEN** a user writes `from alembic_pg_autogen import setup`
- **THEN** the import succeeds

#### Scenario: Listed in \_\_all\_\_

- **WHEN** `alembic_pg_autogen.__all__` is inspected
- **THEN** it contains `"setup"`
