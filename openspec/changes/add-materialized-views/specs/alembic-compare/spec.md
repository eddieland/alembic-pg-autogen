## ADDED Requirements

### Requirement: Materialized views in the comparator pipeline

When `pg_materialized_views` is declared, the comparator SHALL inspect current materialized views via
`inspect_materialized_views`, pass the declared DDL to `canonicalize` as `matview_ddl`, diff the two states, and map
each `MaterializedViewOp` to the corresponding `MigrateOperation` subclass. For each `REPLACE`, the comparator SHALL
read the current object's index DDL via `inspect_matview_indexes` and attach it to the `ReplaceMaterializedViewOp`.

#### Scenario: New materialized view produces a create

- **WHEN** `pg_materialized_views` declares `public.mv` and the database does not contain it
- **THEN** a `CreateMaterializedViewOp` is appended to `upgrade_ops.ops`

#### Scenario: Changed query produces a replace carrying index DDL

- **WHEN** the database contains `public.mv` with a unique index and `pg_materialized_views` declares `public.mv` with a
  different query
- **THEN** a `ReplaceMaterializedViewOp` is appended
- **AND** its `index_ddl` holds the `pg_get_indexdef()` statements of the existing index

#### Scenario: Undeclared materialized view produces a drop

- **WHEN** `pg_materialized_views` is an empty sequence and the database contains a materialized view
- **THEN** a `DropMaterializedViewOp` is appended

#### Scenario: Unchanged materialized view produces nothing

- **WHEN** the declared query matches the database definition after canonicalization
- **THEN** no materialized view operation is appended

#### Scenario: Unmanaged materialized views are untouched

- **WHEN** `pg_materialized_views` is absent or `IGNORED` and the database contains materialized views
- **THEN** the materialized view catalog is not inspected and no materialized view operations are emitted

#### Scenario: Misspelled key is reported

- **WHEN** `context.configure()` is called with `pg_materialized_view=[...]`
- **THEN** the comparator logs a warning naming `pg_materialized_view` and `pg_materialized_views`

#### Scenario: Declared identity parsing

- **WHEN** the comparator filters canonical state to declared objects
- **THEN** each `pg_materialized_views` DDL string is parsed with postgast, whose `CreateTableAsStmt` node with
  `objtype = OBJECT_MATVIEW` yields the `(schema, name)` identity
- **AND** an unqualified name resolves through the connection's `current_schema()`
- **AND** a string that is not a `CREATE MATERIALIZED VIEW` statement raises `ValueError`

## MODIFIED Requirements

### Requirement: Desired-state configuration keys

The comparator SHALL read desired function, trigger, view, and materialized view DDL from `autogen_context.opts` using
the keys `pg_functions`, `pg_triggers`, `pg_views`, and `pg_materialized_views`. Each key SHALL default to `IGNORED` if
absent, leaving that object type unmanaged. A key explicitly set to an empty sequence SHALL continue to declare "there
should be no objects of this type".

#### Scenario: Functions, triggers, and views provided

- **WHEN** `context.configure()` is called with `pg_functions=["CREATE OR REPLACE FUNCTION ..."]`,
  `pg_triggers=["CREATE TRIGGER ..."]`, and `pg_views=["CREATE VIEW ..."]`
- **THEN** the comparator reads these sequences from `autogen_context.opts["pg_functions"]`,
  `autogen_context.opts["pg_triggers"]`, and `autogen_context.opts["pg_views"]`

#### Scenario: Materialized views provided

- **WHEN** `context.configure()` is called with `pg_materialized_views=["CREATE MATERIALIZED VIEW ..."]`
- **THEN** the comparator reads the sequence from `autogen_context.opts["pg_materialized_views"]`

#### Scenario: All keys absent

- **WHEN** `context.configure()` is called without `pg_functions`, `pg_triggers`, `pg_views`, or `pg_materialized_views`
- **THEN** all four resolve to `IGNORED` and the comparator returns `PriorityDispatchResult.CONTINUE` without inspecting
  the database
- **AND** appends no operations to `upgrade_ops.ops`

#### Scenario: Only functions provided

- **WHEN** `context.configure()` is called with `pg_functions=[...]` and no other desired-state key, and the inspected
  schemas contain triggers, views, and materialized views that are not declared anywhere
- **THEN** the comparator processes functions normally
- **AND** the other object types are left unmanaged — their catalogs are not inspected and no operations are emitted for
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

### Requirement: Dependency-safe operation ordering

The comparator SHALL emit operations to `upgrade_ops.ops` in an order that respects dependencies between functions,
views, materialized views, and triggers. Views may reference functions. Materialized views may reference functions and
views. Triggers may reference functions and may be defined on views (INSTEAD OF triggers). PostgreSQL rejects triggers
on materialized views. Drops SHALL proceed in reverse dependency order.

#### Scenario: Upgrade ordering

- **WHEN** the diff produces function, view, materialized view, and trigger operations
- **THEN** operations are appended to `upgrade_ops.ops` in this order:
  1. `DropTriggerOp` instances (frees views and functions for removal)
  1. `DropMaterializedViewOp` instances (frees views and functions for removal)
  1. `DropViewOp` instances (frees functions for removal)
  1. `DropFunctionOp` instances
  1. `CreateFunctionOp` and `ReplaceFunctionOp` instances (must exist before views reference them)
  1. `CreateViewOp` and `ReplaceViewOp` instances (must exist before materialized views or INSTEAD OF triggers reference
     them)
  1. `CreateMaterializedViewOp` and `ReplaceMaterializedViewOp` instances
  1. `CreateTriggerOp` and `ReplaceTriggerOp` instances

#### Scenario: Downgrade ordering via reverse

- **WHEN** Alembic calls `upgrade_ops.reverse_into(downgrade_ops)`
- **THEN** the reversed order produces a valid downgrade sequence (drop triggers → drop materialized views → drop views
  → drop functions → create/replace functions → create/replace views → create/replace materialized views →
  create/replace triggers)

#### Scenario: Only view ops

- **WHEN** the diff produces only view operations (no function, materialized view, or trigger changes)
- **THEN** view ops are emitted directly without empty groups for the other types

#### Scenario: Only function ops

- **WHEN** the diff produces only function operations (no trigger, view, or materialized view changes)
- **THEN** function ops are emitted directly without empty groups for the other types
