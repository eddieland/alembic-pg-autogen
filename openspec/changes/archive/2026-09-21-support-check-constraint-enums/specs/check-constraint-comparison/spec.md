# Spec Delta

## MODIFIED Requirements

### Requirement: Plugin registration

The package SHALL register the check constraint comparator as its own Alembic plugin named
`alembic_pg_autogen.checkconstraints`, separate from `alembic_pg_autogen.compare`.

#### Scenario: Registered at import time

- **WHEN** `import alembic_pg_autogen` runs
- **THEN** a plugin named `alembic_pg_autogen.checkconstraints` is present in Alembic's plugin registry

#### Scenario: Registered as a table-level comparator for PostgreSQL that runs last

- **WHEN** `setup(plugin)` is called
- **THEN** it calls `plugin.add_autogenerate_comparator()` with `compare_target="table"`, compare element
  `"check_constraint_expressions"`, `qualifier="postgresql"`, and `priority=DispatchPriority.LAST`
- **AND** the comparator therefore runs after Alembic's own check constraint plugin for the same table

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

#### Scenario: Changed value set produces drop and NOT VALID add

- **WHEN** the database has `CHECK (status IN ('a', 'b'))` named `ck_orders_status` and the model declares the same name
  over `('a', 'b', 'c')`
- **AND** `pg_check_constraint_validation` is `"deferred"` or absent
- **THEN** a `DropConstraintOp` and a `CreateCheckConstraintNotValidOp` are appended, in that order
- **AND** the generated migration renders `op.create_check_constraint(..., postgresql_not_valid=True)`

#### Scenario: Equivalent expression produces nothing

- **WHEN** the database has `CHECK (amount >= 0)` and the model declares the same constraint written as `amount >= 0`
- **THEN** no operation is emitted, even though the raw texts differ from the catalog's `amount >= 0::numeric`

#### Scenario: Added and removed constraints are left to Alembic

- **WHEN** a named check constraint exists on only one of the two sides
- **THEN** this comparator emits nothing for it, because Alembic's `checkconstraint_byname` plugin handles existence

#### Scenario: Downgrade restores the previous expression

- **WHEN** a changed constraint's operations are reversed for the downgrade
- **THEN** the downgrade drops the constraint and recreates it with the expression read from the catalog

#### Scenario: Reflected constraint records the catalog validation state

- **WHEN** the catalog constraint that a `DropConstraintOp` removes has `convalidated = false`
- **THEN** the constraint the operation carries has `postgresql_not_valid=True`

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

The comparator SHALL consider only named check constraints, including those declared on a column rather than on the
table. It SHALL include a type-bound constraint when SQLAlchemy creates that constraint on PostgreSQL, and it SHALL skip
every other type-bound constraint.

#### Scenario: Unnamed constraints are ignored

- **WHEN** a model declares `CheckConstraint("amount >= 0")` with no name
- **THEN** it is ignored, because it cannot be matched to a catalog constraint by name

#### Scenario: Naming conventions are resolved

- **WHEN** the metadata uses a naming convention such as `{"ck": "ck_%(table_name)s_%(constraint_name)s"}`
- **THEN** the constraint is matched under the name it would actually be created with

#### Scenario: Non-native enum constraints are compared

- **WHEN** a column uses `Enum(native_enum=False, create_constraint=True, name="status")`
- **THEN** the constraint SQLAlchemy derives from it is compared under its resolved name

#### Scenario: Type-bound constraints that PostgreSQL does not create are ignored

- **WHEN** a column uses `Boolean(create_constraint=True)` or a native `Enum(create_constraint=True)`
- **THEN** the derived constraint is ignored, because PostgreSQL never holds it

#### Scenario: Column expressions compile without the table prefix

- **WHEN** a metadata constraint holds a column expression such as `table.c.status.in_(["a"])`
- **THEN** the compiled text is `status IN ('a')`, the same form SQLAlchemy emits in DDL

#### Scenario: Column-level constraints are included

- **WHEN** a check constraint is declared on a `Column` rather than in the `Table` body
- **THEN** it is still compared

#### Scenario: Filters are honored

- **WHEN** `include_name` or `include_object` excludes a constraint with type `"check_constraint"`
- **THEN** no operation is emitted for it

## ADDED Requirements

### Requirement: Type-bound constraints are owned end to end

Alembic's `checkconstraint_byname` plugin excludes type-bound constraints from its metadata side. For each type-bound
constraint that SQLAlchemy creates on PostgreSQL, the comparator SHALL therefore manage existence as well as the
expression.

#### Scenario: Alembic's drop of a declared enum constraint is discarded

- **WHEN** the catalog and the model both hold `ck_orders_status` from `Enum(native_enum=False)`
- **AND** Alembic's plugin appended `DropConstraintOp("ck_orders_status", "orders", type_="check")` to the table's
  operations
- **THEN** the comparator removes that operation before it compares the expression
- **AND** an unchanged expression leaves no operation for `ck_orders_status`

#### Scenario: Drop of a constraint the model no longer declares is kept

- **WHEN** the catalog holds `ck_orders_status` and the model column now uses `create_constraint=False`
- **THEN** Alembic's `DropConstraintOp` stays in the table's operations

#### Scenario: Missing enum constraint is added

- **WHEN** the model declares `Enum(native_enum=False, create_constraint=True, name="ck_orders_status")` and the catalog
  holds no constraint by that name
- **THEN** an `AddConstraintOp` for the constraint is appended, honoring `include_name` and `include_object`

#### Scenario: Missing constraint that declares NOT VALID is added NOT VALID

- **WHEN** a type-bound constraint that PostgreSQL creates is absent from the catalog and its model declaration carries
  `postgresql_not_valid=True`
- **THEN** a `CreateCheckConstraintNotValidOp` is appended, so the rendered migration keeps the flag

#### Scenario: Missing plain constraint stays Alembic's job

- **WHEN** a constraint that is not type-bound exists in the model only
- **THEN** the comparator emits nothing for it

### Requirement: Deferred validation of changed value sets

The comparator SHALL classify each changed expression with `classify_value_set_change(current, desired)`. When the
result is `WIDENING`, `NARROWING`, or `DISJOINT` and the validation mode is `"deferred"`, it SHALL emit
`CreateCheckConstraintNotValidOp` in place of `AddConstraintOp`. It SHALL also emit `CreateCheckConstraintNotValidOp`
when the metadata constraint declares `postgresql_not_valid=True`, whatever the classification.

#### Scenario: Widening carries no removed values

- **WHEN** the value set widens from `('a', 'b')` to `('a', 'b', 'c')`
- **THEN** the emitted operation has `removed_values == ()`
- **AND** the rendered migration holds no backfill comment

#### Scenario: Narrowing carries the removed values

- **WHEN** the value set narrows from `('a', 'b', 'c')` to `('a', 'b')` on column `status`
- **THEN** the emitted operation has `column == "status"` and `removed_values == ("c",)`
- **AND** the rendered migration holds a comment above `op.create_check_constraint(...)` that names the column, the
  removed value, the constraint, and the need for a backfill before the validation revision

#### Scenario: Disjoint change carries the removed values

- **WHEN** the value set changes from `('a', 'b')` to `('b', 'c')`
- **THEN** the emitted operation has `removed_values == ("a",)`

#### Scenario: Expression that is not a value set keeps the validating path

- **WHEN** the expression changes from `amount >= 0` to `amount > 0`
- **THEN** a `DropConstraintOp` and a plain `AddConstraintOp` are emitted, as before this change

#### Scenario: Immediate mode keeps the validating path

- **WHEN** `pg_check_constraint_validation` is `"immediate"` and a value set changes
- **THEN** a `DropConstraintOp` and a plain `AddConstraintOp` are emitted

#### Scenario: Metadata declares NOT VALID

- **WHEN** the model declares `CheckConstraint("amount > 0", name="ck", postgresql_not_valid=True)` and the catalog
  expression differs
- **THEN** a `CreateCheckConstraintNotValidOp` is emitted, so the rendered migration keeps the flag

### Requirement: Validation state comparison

For each shared constraint whose expression is unchanged, the comparator SHALL emit `ValidateConstraintOp` when the
catalog reports `validated == False` and the metadata constraint does not declare `postgresql_not_valid=True`. It SHALL
do so in both validation modes.

#### Scenario: NOT VALID constraint that the model wants validated

- **WHEN** the catalog holds `ck_orders_status` with `convalidated = false` and the same expression as the model
- **AND** the model constraint does not set `postgresql_not_valid`
- **THEN** a `ValidateConstraintOp("ck_orders_status", "orders", schema=...)` is appended

#### Scenario: NOT VALID constraint that the model keeps NOT VALID

- **WHEN** the catalog holds a constraint with `convalidated = false` and the model declares `postgresql_not_valid=True`
  on it
- **THEN** no operation is emitted

#### Scenario: Validated constraint

- **WHEN** the catalog holds a constraint with `convalidated = true` and the expression is unchanged
- **THEN** no operation is emitted, whatever the model's `postgresql_not_valid` setting

#### Scenario: Changed expression emits no validation

- **WHEN** the catalog holds a `NOT VALID` constraint and the model expression differs
- **THEN** the comparator emits the drop and add pair and no `ValidateConstraintOp`

#### Scenario: Two runs converge

- **WHEN** the first autogenerate run emits a `NOT VALID` addition, the migration runs, and autogenerate runs again
- **THEN** the second run emits exactly one `ValidateConstraintOp` for that constraint
- **AND** a third run after that migration emits nothing

#### Scenario: Filters are honored

- **WHEN** `include_name` or `include_object` excludes the constraint
- **THEN** no `ValidateConstraintOp` is emitted for it

### Requirement: Validation mode configuration

The comparator SHALL read `pg_check_constraint_validation` from `autogen_context.opts`. It SHALL accept `"deferred"` and
`"immediate"`, and it SHALL default to `"deferred"` when the key is absent.

#### Scenario: Default mode

- **WHEN** `context.configure()` receives no `pg_check_constraint_validation`
- **THEN** the comparator behaves as if the value were `"deferred"`

#### Scenario: Invalid value

- **WHEN** `pg_check_constraint_validation="later"` is configured
- **THEN** the comparator raises `ValueError` naming the key and the accepted values

#### Scenario: Misspelled key warns

- **WHEN** `context.configure(pg_check_constraint_validaton="immediate")` is used
- **THEN** a warning names `pg_check_constraint_validation` as the intended key
