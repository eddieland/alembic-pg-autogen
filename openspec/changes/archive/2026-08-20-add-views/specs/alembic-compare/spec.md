## MODIFIED Requirements

### Requirement: Desired-state configuration keys

The comparator SHALL read desired function, trigger, and view DDL from `autogen_context.opts` using the keys
`pg_functions`, `pg_triggers`, and `pg_views`. All three keys SHALL default to empty sequences if absent.

#### Scenario: Functions, triggers, and views provided

- **WHEN** `context.configure()` is called with `pg_functions=["CREATE OR REPLACE FUNCTION ..."]`,
  `pg_triggers=["CREATE TRIGGER ..."]`, and `pg_views=["CREATE VIEW ..."]`
- **THEN** the comparator reads these sequences from `autogen_context.opts["pg_functions"]`,
  `autogen_context.opts["pg_triggers"]`, and `autogen_context.opts["pg_views"]`

#### Scenario: Keys absent

- **WHEN** `context.configure()` is called without `pg_functions`, `pg_triggers`, or `pg_views`
- **THEN** the comparator treats all three as empty sequences and produces no operations

#### Scenario: Only views provided

- **WHEN** `context.configure()` is called with `pg_views=[...]` but no `pg_functions` or `pg_triggers`
- **THEN** the comparator processes views normally and treats functions and triggers as empty desired sets
- **AND** existing database functions and triggers in managed schemas are NOT dropped

#### Scenario: Only functions provided

- **WHEN** `context.configure()` is called with `pg_functions=[...]` but no `pg_triggers` or `pg_views`
- **THEN** the comparator processes functions normally and treats triggers and views as empty desired sets
- **AND** existing database triggers and views in managed schemas are NOT dropped (only explicitly declared objects are
  managed)

### Requirement: Comparator pipeline orchestration

The comparator SHALL execute the full inspect-canonicalize-diff pipeline when desired-state DDL is provided.

#### Scenario: Full pipeline execution

- **WHEN** the comparator fires with non-empty `pg_functions`, `pg_triggers`, and/or `pg_views`
- **THEN** it executes these steps in order:
  1. Inspect current functions, triggers, and views from the database via `inspect_functions`, `inspect_triggers`, and
     `inspect_views`
  1. Construct a `CanonicalState` from the inspection results
  1. Canonicalize the desired DDL via `canonicalize(conn, pg_functions, pg_triggers, pg_views)`
  1. Diff the current state against the desired state via `diff(current, desired)`
  1. Map each `FunctionOp`/`TriggerOp`/`ViewOp` to the corresponding `MigrateOperation` subclass
  1. Append the operations to `upgrade_ops.ops`

#### Scenario: No changes detected

- **WHEN** the current database state matches the desired state exactly
- **THEN** the comparator appends no operations to `upgrade_ops.ops`

#### Scenario: Comparator returns CONTINUE

- **WHEN** the comparator finishes (whether or not it emitted ops)
- **THEN** it returns `PriorityDispatchResult.CONTINUE` to allow other schema-level comparators to run

### Requirement: Dependency-safe operation ordering

The comparator SHALL emit operations to `upgrade_ops.ops` in an order that respects dependencies between functions,
views, and triggers. Views may reference functions. Triggers may reference functions and may be defined on views
(INSTEAD OF triggers). Function creation/replacement SHALL precede view creation/replacement, which SHALL precede
trigger creation/replacement. Drops SHALL proceed in reverse dependency order.

#### Scenario: Upgrade ordering

- **WHEN** the diff produces function, view, and trigger operations
- **THEN** operations are appended to `upgrade_ops.ops` in this order:
  1. `DropTriggerOp` instances (frees views and functions for removal)
  1. `DropViewOp` instances (frees functions for removal)
  1. `DropFunctionOp` instances
  1. `CreateFunctionOp` and `ReplaceFunctionOp` instances (must exist before views reference them)
  1. `CreateViewOp` and `ReplaceViewOp` instances (must exist before INSTEAD OF triggers reference them)
  1. `CreateTriggerOp` and `ReplaceTriggerOp` instances

#### Scenario: Downgrade ordering via reverse

- **WHEN** Alembic calls `upgrade_ops.reverse_into(downgrade_ops)`
- **THEN** the reversed order produces a valid downgrade sequence (drop triggers → drop views → drop functions →
  create/replace functions → create/replace views → create/replace triggers)

#### Scenario: Only view ops

- **WHEN** the diff produces only view operations (no function or trigger changes)
- **THEN** view ops are emitted directly without empty function or trigger op groups

#### Scenario: Only function ops

- **WHEN** the diff produces only function operations (no trigger or view changes)
- **THEN** function ops are emitted directly without empty trigger or view op groups

### Requirement: Schema filtering

The comparator SHALL use the `schemas` parameter provided by Alembic's dispatch to filter which database objects are
inspected and which desired-state objects are included in the diff.

#### Scenario: Default schema only

- **WHEN** Alembic dispatches with `schemas={None}` (the default, representing the connection's default schema)
- **THEN** the comparator inspects only the default schema and filters canonicalized desired state to that schema

#### Scenario: Multiple schemas

- **WHEN** Alembic dispatches with `schemas={None, "audit", "reporting"}`
- **THEN** the comparator inspects functions, triggers, and views in all three schemas
- **AND** canonicalized desired-state objects outside these schemas are excluded from the diff
