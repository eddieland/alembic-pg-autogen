## MODIFIED Requirements

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

## ADDED Requirements

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
