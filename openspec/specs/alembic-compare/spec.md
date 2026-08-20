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

The comparator SHALL read desired function, trigger, and view DDL from `autogen_context.opts` using the keys
`pg_functions`, `pg_triggers`, and `pg_views`. Each key SHALL default to `IGNORED` if absent, leaving that object type
unmanaged. A key explicitly set to an empty sequence SHALL continue to declare "there should be no objects of this
type".

#### Scenario: Functions, triggers, and views provided

- **WHEN** `context.configure()` is called with `pg_functions=["CREATE OR REPLACE FUNCTION ..."]`,
  `pg_triggers=["CREATE TRIGGER ..."]`, and `pg_views=["CREATE VIEW ..."]`
- **THEN** the comparator reads these sequences from `autogen_context.opts["pg_functions"]`,
  `autogen_context.opts["pg_triggers"]`, and `autogen_context.opts["pg_views"]`

#### Scenario: All keys absent

- **WHEN** `context.configure()` is called without `pg_functions`, `pg_triggers`, or `pg_views`
- **THEN** all three resolve to `IGNORED` and the comparator returns `PriorityDispatchResult.CONTINUE` without
  inspecting the database
- **AND** appends no operations to `upgrade_ops.ops`

#### Scenario: Only functions provided

- **WHEN** `context.configure()` is called with `pg_functions=[...]` but no `pg_triggers` or `pg_views`, and the
  inspected schemas contain triggers and views that are not declared anywhere
- **THEN** the comparator processes functions normally
- **AND** triggers and views are left unmanaged — their catalogs are not inspected and no operations are emitted for
  them, in particular no `DROP`

#### Scenario: Only views provided

- **WHEN** `context.configure()` is called with `pg_views=[...]` but no `pg_functions` or `pg_triggers`, and the
  inspected schemas contain functions and triggers
- **THEN** the comparator processes views normally
- **AND** the existing functions and triggers are left in place

#### Scenario: Empty sequence still drops

- **WHEN** `context.configure()` is called with `pg_views=[]` and the inspected schemas contain views
- **THEN** `DropViewOp` instances are emitted for those views, unchanged from before the default changed

#### Scenario: Omitted types are reported

- **WHEN** at least one object type is declared and at least one is left unmanaged, whether by an explicit `IGNORED` or
  by omission
- **THEN** the comparator logs the unmanaged object types at `INFO` level

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

### Requirement: Unmanaged object types

The comparator SHALL accept `IGNORED` as the value of `pg_functions`, `pg_triggers`, or `pg_views`, marking that object
type unmanaged. For an unmanaged object type the comparator SHALL NOT inspect the catalog, SHALL treat the desired set
as empty without parsing any DDL, and SHALL emit no operations — in particular no `DROP` for existing objects of that
type.

#### Scenario: Ignored views are not dropped

- **WHEN** `context.configure()` is called with `pg_functions=[...]` and `pg_views=IGNORED`, and the inspected schemas
  contain views that are not declared anywhere
- **THEN** function operations are emitted as usual
- **AND** no view operations are emitted, and the existing views are left in place

#### Scenario: Ignored functions and triggers are not dropped

- **WHEN** `context.configure()` is called with `pg_views=[...]`, `pg_functions=IGNORED`, and `pg_triggers=IGNORED`, and
  the inspected schemas contain functions and triggers
- **THEN** only view operations are emitted
- **AND** the existing functions and triggers are left in place

#### Scenario: All object types ignored

- **WHEN** `pg_functions`, `pg_triggers`, and `pg_views` are all `IGNORED`
- **THEN** the comparator returns `PriorityDispatchResult.CONTINUE` without inspecting the database
- **AND** appends no operations to `upgrade_ops.ops`

#### Scenario: Ignored types are reported

- **WHEN** at least one object type is ignored and at least one is managed
- **THEN** the comparator logs the ignored object types at `INFO` level

#### Scenario: alembic-utils entities alongside the sentinel

- **WHEN** a key holds a sequence of `SQLCreatable` objects and another key holds `IGNORED`
- **THEN** the sequence is resolved to DDL strings as usual and the sentinel is passed through untouched

### Requirement: Unrecognized configuration options

The comparator SHALL inspect `autogen_context.opts` for keys beginning with `pg_` that are not recognized desired-state
keys, and SHALL log a warning for any that closely resemble a recognized key, naming the recognized key. Unrecognized
keys that do not closely resemble one SHALL be left alone, and no unrecognized key SHALL raise.

#### Scenario: Misspelled key is reported

- **WHEN** `context.configure()` is called with `pg_view=[...]` instead of `pg_views=[...]`
- **THEN** the comparator logs a warning at `WARNING` level naming both `pg_view` and `pg_views`
- **AND** autogenerate proceeds, with views left unmanaged

#### Scenario: Unrelated option is not reported

- **WHEN** `autogen_context.opts` contains a `pg_` key that does not closely resemble `pg_functions`, `pg_triggers`, or
  `pg_views` — for example another plugin's option
- **THEN** the comparator logs no warning for it

#### Scenario: Typos are reported even when nothing is managed

- **WHEN** the only `pg_*` key present is a misspelled one, so every recognized object type is unmanaged
- **THEN** the warning is still logged before the comparator short-circuits
