## ADDED Requirements

### Requirement: Plugin registration

The package SHALL register the check constraint comparator as its own Alembic plugin named
`alembic_pg_autogen.checkconstraints`, separate from `alembic_pg_autogen.compare`.

#### Scenario: Registered at import time

- **WHEN** `import alembic_pg_autogen` runs
- **THEN** a plugin named `alembic_pg_autogen.checkconstraints` is present in Alembic's plugin registry

#### Scenario: Registered as a table-level comparator for PostgreSQL

- **WHEN** `setup(plugin)` is called
- **THEN** it calls `plugin.add_autogenerate_comparator()` with `compare_target="table"`, compare element
  `"check_constraint_expressions"`, and `qualifier="postgresql"`

#### Scenario: Enabled by the usual wildcard

- **WHEN** `context.configure(autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"])` is used
- **THEN** both this plugin and the function/trigger/view comparator are enabled

#### Scenario: Independently disableable

- **WHEN** `~alembic_pg_autogen.checkconstraints` is included in `autogenerate_plugins`
- **THEN** check constraint comparison is skipped
- **AND** function, trigger, and view comparison still runs

### Requirement: Compare expressions of constraints declared in target metadata

The comparator SHALL compare, for each table present in both the database and `target_metadata`, the expression of every
named check constraint whose name appears on both sides. It SHALL NOT consider constraints that exist on only one side.

#### Scenario: Changed expression produces drop and add

- **WHEN** the database has `CHECK (amount >= 0)` named `ck_orders_amount` and the model declares
  `CheckConstraint("amount > 0", name="ck_orders_amount")`
- **THEN** a `DropConstraintOp` for the reflected constraint and an `AddConstraintOp` for the metadata constraint are
  appended, in that order
- **AND** the generated migration renders `op.drop_constraint(...)` followed by `op.create_check_constraint(...)`

#### Scenario: Equivalent expression produces nothing

- **WHEN** the database has `CHECK (amount >= 0)` and the model declares the same constraint written as `amount >= 0`
- **THEN** no operation is emitted, even though the raw texts differ from the catalog's `amount >= 0::numeric`

#### Scenario: Added and removed constraints are left to Alembic

- **WHEN** a named check constraint exists on only one of the two sides
- **THEN** this comparator emits nothing for it, because Alembic's `checkconstraint_byname` plugin handles existence

#### Scenario: Downgrade restores the previous expression

- **WHEN** a changed constraint's operations are reversed for the downgrade
- **THEN** the downgrade drops the constraint and recreates it with the expression read from the catalog

#### Scenario: Tables on only one side are skipped

- **WHEN** either `conn_table` or `metadata_table` is `None`
- **THEN** the comparator returns `PriorityDispatchResult.CONTINUE` without emitting operations, because the constraint
  travels with the `CREATE TABLE` or `DROP TABLE`

#### Scenario: Offline autogenerate is skipped

- **WHEN** `autogen_context.connection` is `None`
- **THEN** the comparator returns `PriorityDispatchResult.CONTINUE` without emitting operations

#### Scenario: Comparator returns CONTINUE

- **WHEN** the comparator finishes, whether or not it emitted operations
- **THEN** it returns `PriorityDispatchResult.CONTINUE` so other table-level comparators still run

### Requirement: Constraint selection rules

The comparator SHALL consider only named, non-type-bound check constraints, including those declared on a column rather
than on the table.

#### Scenario: Unnamed constraints are ignored

- **WHEN** a model declares `CheckConstraint("amount >= 0")` with no name
- **THEN** it is ignored, because it cannot be matched to a catalog constraint by name

#### Scenario: Naming conventions are resolved

- **WHEN** the metadata uses a naming convention such as `{"ck": "ck_%(table_name)s_%(constraint_name)s"}`
- **THEN** the constraint is matched under the name it would actually be created with

#### Scenario: Type-bound constraints are ignored

- **WHEN** a column uses a type that generates its own check constraint, such as `Enum(native_enum=False)`
- **THEN** that constraint is ignored, matching Alembic's own comparator

#### Scenario: Column-level constraints are included

- **WHEN** a check constraint is declared on a `Column` rather than in the `Table` body
- **THEN** it is still compared

#### Scenario: Filters are honored

- **WHEN** `include_name` or `include_object` excludes a constraint with type `"check_constraint"`
- **THEN** no operation is emitted for it

### Requirement: Failure degrades to "unchanged"

The comparator SHALL treat any constraint it cannot compile or normalize as unchanged, logging a warning rather than
raising.

#### Scenario: Uncompilable metadata expression

- **WHEN** a metadata constraint's expression cannot be compiled to PostgreSQL SQL
- **THEN** a warning is logged and no operation is emitted for that constraint
- **AND** other constraints on the table are still compared

#### Scenario: Expression that will not apply

- **WHEN** the normalization probe fails, for example because the expression references a column that does not exist yet
- **THEN** a warning is logged and no operation is emitted for that constraint
