# Spec Delta

## MODIFIED Requirements

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

### Requirement: Unnamed constraints are paired by canonical expression

For each table on both sides, the comparator SHALL collect the unnamed metadata check constraints. A constraint is
unnamed when its name resolves to `None` after naming conventions. The comparator SHALL pair each one with a reflected
constraint whose canonical expression is equal. Only a reflected constraint whose name no named metadata constraint
uses, and that `include_name` accepts, SHALL be a candidate.

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
- **THEN** `min(n, m)` copies pair, metadata copies in declaration order and catalog copies in name order
- **AND** a warning that names both counts is logged when `n` and `m` differ
- **AND** the remaining metadata copies get adds and the remaining catalog copies keep their drops

#### Scenario: A constraint without a canonical expression is left alone

- **WHEN** candidates exist and the canonicalization probe fails for a metadata constraint
- **THEN** the comparator emits no add for it
- **AND** Alembic's drops stay

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

#### Scenario: A rejected name probe adds nothing

- **WHEN** `choose_check_constraint_names` returns `None` for the expression
- **THEN** no add is appended

#### Scenario: Constraint of a column Alembic is adding

- **WHEN** an unnamed type-bound constraint references a column that the database table lacks
- **THEN** the comparator emits nothing for it, because `op.add_column()` creates the constraint with the column

#### Scenario: Text constraint on a new column is added on the next run

- **WHEN** a column-level unnamed `CheckConstraint("qty >= 0")` sits on a column that the database table lacks
- **THEN** the first run emits `add_column` and no add for the constraint
- **AND** the next run adds the constraint as `orders_qty_check`
