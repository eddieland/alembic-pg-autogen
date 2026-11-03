## ADDED Requirements

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

#### Scenario: Narrowed value set produces drop and NOT VALID add

- **WHEN** the database has `CHECK (status IN ('a', 'b', 'c'))` named `ck_orders_status` and the model declares the same
  name over `('a', 'b')`
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

The comparator SHALL compare named check constraints by name, including those declared on a column rather than on the
table. It SHALL pair unnamed check constraints by canonical expression, as the requirement "Unnamed constraints are
paired by canonical expression" states. It SHALL include a type-bound constraint when SQLAlchemy creates that constraint
on PostgreSQL, and it SHALL skip every other type-bound constraint.

#### Scenario: Unnamed constraints are paired by expression

- **WHEN** a model declares `CheckConstraint("amount >= 0")` with no name
- **THEN** it is not matched by name
- **AND** it is paired by its canonical expression instead

#### Scenario: Naming conventions are resolved

- **WHEN** the metadata uses a naming convention such as `{"ck": "ck_%(table_name)s_%(constraint_name)s"}`
- **THEN** the constraint is matched under the name it would actually be created with

#### Scenario: Catalog names are final under a naming convention

- **WHEN** the metadata uses the naming convention `{"ck": "ck_%(table_name)s_%(constraint_name)s"}` and the expression
  of `ck_o_foo` changes
- **THEN** the migration renders the catalog name as `op.f('ck_o_foo')` in the drop, the add, and the downgrade
- **AND** the upgrade drops `ck_o_foo`, not `ck_o_ck_o_foo`

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

### Requirement: Type-bound constraints are owned end to end

Before Alembic 1.19.2, Alembic's `checkconstraint_byname` plugin excludes type-bound constraints from its metadata side.
Later releases include them. For each type-bound constraint that SQLAlchemy creates on PostgreSQL, the comparator SHALL
therefore manage existence as well as the expression, and it SHALL leave one add in the migration.

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

#### Scenario: Alembic's add of a missing enum constraint is replaced

- **WHEN** Alembic's plugin appended a `CreateCheckConstraintOp` for a missing type-bound constraint
- **AND** the comparator adds the same constraint
- **THEN** the comparator removes Alembic's operation, so the migration holds one add
- **AND** an add that Alembic emitted for another name stays

#### Scenario: Alembic's add for a column Alembic is adding is discarded

- **WHEN** Alembic's plugin appended a `CreateCheckConstraintOp` for a type-bound constraint of a new column
- **THEN** the comparator removes that operation, because `op.add_column()` creates the constraint

#### Scenario: Constraint of a column Alembic is adding is left to add_column

- **WHEN** the model declares a non-native `Enum` column that the database table lacks
- **THEN** the comparator emits nothing for its constraint, because `op.add_column()` creates the constraint with the
  column

#### Scenario: Missing plain constraint stays Alembic's job

- **WHEN** a constraint that is not type-bound exists in the model only
- **THEN** the comparator emits nothing for it

### Requirement: Unnamed constraints are paired by canonical expression

For each table on both sides, the comparator SHALL collect the unnamed metadata check constraints. A constraint is
unnamed when its name resolves to `None` after naming conventions. The comparator SHALL pair each one with a reflected
constraint whose canonical expression is equal. Only a reflected constraint whose name no named metadata constraint uses
SHALL be a candidate.

#### Scenario: Alembic's drop of a paired constraint is discarded

- **WHEN** the model declares `CheckConstraint("amount > 0")` and the catalog holds `orders_amount_check` with an equal
  canonical expression
- **AND** Alembic's plugin appended `DropConstraintOp("orders_amount_check", "orders", type_="check")`
- **THEN** the comparator removes that operation
- **AND** no operation remains for the constraint

#### Scenario: Text that matches the catalog needs no round trip

- **WHEN** the compiled metadata text equals a candidate's deparsed expression, ignoring whitespace
- **THEN** the comparator pairs them without calling `canonicalize_check_constraints`

#### Scenario: An unnamed enum constraint pairs

- **WHEN** a column uses `Enum("new", "done", native_enum=False, create_constraint=True)` without a name
- **THEN** its constraint pairs with the catalog constraint `orders_status_check`

#### Scenario: A reflected constraint without a pair keeps its drop

- **WHEN** the catalog holds `orders_id_check`, added in raw SQL, and no model constraint has an equal expression
- **THEN** Alembic's `DropConstraintOp` for `orders_id_check` stays

#### Scenario: A constraint claimed by name does not pair

- **WHEN** the model declares `ck_orders_amount` and an unnamed constraint, both `amount > 0`, and the catalog holds
  only `ck_orders_amount`
- **THEN** the unnamed constraint gets an add

#### Scenario: A NOT VALID pair is validated

- **WHEN** a pair's catalog row reads `convalidated = false` and the model does not declare `postgresql_not_valid=True`
- **THEN** a `ValidateConstraintOp` under the reflected name is appended, honoring `include_object`

#### Scenario: Duplicate expressions pair in order

- **WHEN** an expression occurs `n` times in the model and `m` times among the candidates
- **THEN** `min(n, m)` copies pair, metadata copies in declaration order and catalog copies validated first, then in
  name order
- **AND** a warning that names both counts is logged when `n` and `m` differ
- **AND** the remaining metadata copies get adds and the remaining catalog copies keep their drops

#### Scenario: A candidate that include_name excludes still pairs

- **WHEN** `include_name` excludes the reflected `o_amount_check`, and the model declares an unnamed `amount > 0`
- **THEN** the two pair, and the comparator emits no operation for them
- **AND** no duplicate `o_amount_check1` is added

#### Scenario: A failed probe keeps the leftover constraints

- **WHEN** candidates exist and the canonicalization probe fails for a metadata constraint
- **THEN** the comparator emits no add for it
- **AND** it discards Alembic's drops of the candidates that no constraint paired with, and logs a warning
- **AND** the warning says that those constraints stay until every unnamed model constraint can be compared
- **AND** the comparator appends a `NoOp` whose reason names the kept constraints and the rejected expression, so the
  migration renders `pass  # Kept check constraints ...` and `alembic check` fails

#### Scenario: A failed probe does not cost the others their pairs

- **WHEN** the catalog holds `amount >= 0` and the model declares `amount >= 0` and `qty > 0` on a new column `qty`
- **THEN** the `amount` constraint pairs, and Alembic's drop of it is discarded

#### Scenario: An unnamed enum narrowing is added NOT VALID

- **WHEN** the catalog holds `o_status_check` for `status IN ('a', 'b', 'c')` and the model declares
  `Enum("a", "b", native_enum=False, create_constraint=True)` without a name
- **THEN** the comparator emits `DropCheckConstraintOp("o_status_check")` and a `CreateCheckConstraintNotValidOp` under
  the same name, with the backfill comment for `'c'`

### Requirement: Missing unnamed constraints are added under the PostgreSQL name

For each unnamed metadata constraint without a pair, the comparator SHALL call `choose_check_constraint_names` and SHALL
append an add under the returned name.

#### Scenario: Missing constraint is added and can be downgraded

- **WHEN** the catalog holds `orders_amount_check` for `amount > 0` and the model adds an unnamed `amount < 100`
- **THEN** the migration renders `op.create_check_constraint('orders_amount_check1', 'orders', 'amount < 100')`
- **AND** the downgrade renders `op.drop_constraint('orders_amount_check1', 'orders', type_='check')`

#### Scenario: Missing constraint that declares NOT VALID is added NOT VALID

- **WHEN** the unpaired constraint declares `postgresql_not_valid=True`
- **THEN** a `CreateCheckConstraintNotValidOp` under the chosen name is appended

#### Scenario: Filters are honored

- **WHEN** `include_name` rejects the chosen name, or `include_object` rejects the constraint
- **THEN** no add is appended

#### Scenario: A named add of the same migration is avoided

- **WHEN** the model declares `CheckConstraint("amount > 0", name="o_amount_check")`, which the catalog lacks, and an
  unnamed `amount < 100`
- **THEN** the unnamed add uses `o_amount_check1`, and the upgrade succeeds

#### Scenario: A rejected name probe adds nothing

- **WHEN** `choose_check_constraint_names` returns `None` for the expression
- **THEN** no add is appended

#### Scenario: Constraint of a column Alembic is adding

- **WHEN** an unnamed type-bound constraint references a column that the database table lacks
- **THEN** the comparator emits nothing for it, because `op.add_column()` creates the constraint with the column

#### Scenario: Text constraint on a new column is added on the next run

- **WHEN** a column-level unnamed `CheckConstraint("qty >= 0")` sits on a column that the database table lacks
- **THEN** the first run emits `add_column` and no add for the constraint, and logs a warning
- **AND** the next run adds the constraint as `orders_qty_check`

#### Scenario: Column-level constraint on a new table is added on the next run

- **WHEN** the model declares a new table with a column-level unnamed `CheckConstraint("amount > 0")`
- **THEN** the first run emits `create_table`, which leaves the constraint out, and the comparator logs a warning
- **AND** the next run adds the constraint
- **AND** a table-level unnamed constraint and an `Enum` constraint of the new table get no warning

#### Scenario: Column expression constraint on a new column warns

- **WHEN** an unnamed constraint that is not type-bound, such as `CheckConstraint(table.c.qty > 0)`, references a column
  that the database table lacks
- **THEN** the comparator emits nothing for it and logs a warning

### Requirement: Emitted operations keep the SQL text exactly

Each check constraint add that the comparator emits SHALL be a `CreateCheckConstraintTextOp` or a
`CreateCheckConstraintNotValidOp`, and each drop SHALL be a `DropCheckConstraintOp`.

#### Scenario: A percent sign renders once

- **WHEN** an add holds `code LIKE 'A%'` and the connection uses psycopg
- **THEN** the migration renders `"code LIKE 'A%'"`, and the catalog stores `'A%'`

#### Scenario: A POSIX character class stays unchanged

- **WHEN** an add holds `a ~ '^[[:alpha:]]+$'`
- **THEN** the migration renders the expression unchanged, and the catalog stores the same rule

#### Scenario: A colon is escaped for text()

- **WHEN** an add holds `code IN ('a :b', 'c')`
- **THEN** the migration renders the condition with `\:b`, and the upgrade creates the constraint with `'a :b'`

#### Scenario: The downgrade restores the catalog text

- **WHEN** the comparator drops `ck_o_code`, which holds `code LIKE 'A%' OR code = 'a :b'`
- **THEN** the downgrade re-creates `ck_o_code` with the same catalog expression

#### Scenario: The NOT VALID keyword appears once

- **WHEN** Alembic 1.20 renders the dialect keywords of the add itself
- **THEN** the migration holds `postgresql_not_valid=True` once, and a validating add holds no `postgresql_not_valid`

### Requirement: Deferred validation of changed value sets

The comparator SHALL classify each changed expression with `classify_value_set_change(current, desired)`. When the
result is `NARROWING` or `DISJOINT` and the validation mode is `"deferred"`, it SHALL emit
`CreateCheckConstraintNotValidOp` in place of `AddConstraintOp`. A `WIDENING` SHALL keep the validating
`AddConstraintOp`, because no existing row can violate a superset. It SHALL also emit `CreateCheckConstraintNotValidOp`
when the metadata constraint declares `postgresql_not_valid=True`, whatever the classification.

#### Scenario: Widening keeps the validating path

- **WHEN** the value set widens from `('a', 'b')` to `('a', 'b', 'c')`
- **THEN** a `DropConstraintOp` and a plain `AddConstraintOp` are emitted
- **AND** the rendered migration holds no `postgresql_not_valid` and no backfill comment

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
