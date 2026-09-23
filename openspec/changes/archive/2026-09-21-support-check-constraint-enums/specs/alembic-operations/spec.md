# Spec Delta

## ADDED Requirements

### Requirement: ValidateConstraintOp type

The module SHALL provide a `ValidateConstraintOp` class extending `MigrateOperation` that represents
`ALTER TABLE ... VALIDATE CONSTRAINT ...` on one PostgreSQL table.

#### Scenario: ValidateConstraintOp fields

- **WHEN** `ValidateConstraintOp("ck_orders_status", "orders", schema="public")` is constructed
- **THEN** it stores `constraint_name`, `table_name`, and `schema`
- **AND** `schema` defaults to `None`

#### Scenario: ValidateConstraintOp reverse

- **WHEN** `reverse()` is called on a `ValidateConstraintOp`
- **THEN** it returns a `NoOp` whose `reason` names the constraint and states that PostgreSQL cannot mark a validated
  constraint as not validated
- **AND** it does not raise

#### Scenario: ValidateConstraintOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called with `schema="public"`, `table_name="orders"`, and
  `constraint_name="ck_orders_status"`
- **THEN** it returns `("validate_constraint", "public", "orders", "ck_orders_status")`

### Requirement: CreateCheckConstraintNotValidOp type

The module SHALL provide a `CreateCheckConstraintNotValidOp` class extending Alembic's `CreateCheckConstraintOp`. It
SHALL force `postgresql_not_valid=True` into the operation's keyword arguments, and it SHALL carry `column`
(`str | None`, default `None`) and `removed_values` (`tuple[str, ...]`, default `()`).

#### Scenario: Keyword forced

- **WHEN** `CreateCheckConstraintNotValidOp("ck", "orders", "status IN ('a')")` is constructed
- **THEN** `op.kw["postgresql_not_valid"]` is `True`
- **AND** `op.to_constraint().dialect_options["postgresql"]["not_valid"]` is `True`

#### Scenario: Built from a metadata constraint

- **WHEN** `CreateCheckConstraintNotValidOp.from_constraint(constraint)` is called with a named `CheckConstraint`
- **THEN** the result is a `CreateCheckConstraintNotValidOp` with the constraint's name, table, schema, and expression

#### Scenario: Reverse is a drop

- **WHEN** `reverse()` is called
- **THEN** it returns a `DropConstraintOp` for the same constraint name and table

### Requirement: NoOp type

The module SHALL provide a `NoOp` class extending `MigrateOperation` that executes nothing and carries a `reason`
string.

#### Scenario: NoOp fields and reverse

- **WHEN** `NoOp("constraint ck stays validated")` is constructed
- **THEN** `reason` holds that string
- **AND** `reverse()` returns a `NoOp` with the same reason

#### Scenario: NoOp to_diff_tuple

- **WHEN** `to_diff_tuple()` is called
- **THEN** it returns `("noop", reason)`

### Requirement: Public exports for validation operations

The package SHALL export `ValidateConstraintOp`, `CreateCheckConstraintNotValidOp`, `NoOp`, `ValueSet`,
`ValueSetChange`, `parse_value_set`, `compare_value_sets`, and `classify_value_set_change` from `alembic_pg_autogen`.

#### Scenario: Names in `__all__`

- **WHEN** `alembic_pg_autogen.__all__` is inspected
- **THEN** each of the names above is present and importable from the package
