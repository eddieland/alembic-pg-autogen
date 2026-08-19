## ADDED Requirements

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

#### Scenario: Empty sequence still drops

- **WHEN** `context.configure()` is called with `pg_views=[]` and the inspected schemas contain views
- **THEN** `DropViewOp` instances are emitted for those views, unchanged from before the sentinel existed

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
