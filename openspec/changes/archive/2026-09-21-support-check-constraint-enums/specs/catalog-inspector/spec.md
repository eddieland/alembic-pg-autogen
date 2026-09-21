# Spec Delta

## MODIFIED Requirements

### Requirement: CheckConstraintInfo type

The module SHALL provide a `CheckConstraintInfo` NamedTuple representing a single table-level `CHECK` constraint as
loaded from the catalog.

#### Scenario: CheckConstraintInfo fields

- **WHEN** a `CheckConstraintInfo` instance is created
- **THEN** it has the following fields:
  - `schema` (`str`): the constrained table's namespace from `pg_namespace.nspname`
  - `table_name` (`str`): the constrained table from `pg_class.relname`
  - `name` (`str`): the constraint name from `pg_constraint.conname`
  - `expression` (`str`): the normalized check expression from `pg_get_expr(conbin, conrelid, true)`
  - `validated` (`bool`, default `True`): the value of `pg_constraint.convalidated`

#### Scenario: Four-field construction keeps working

- **WHEN** `CheckConstraintInfo("s", "t", "n", "e")` is constructed
- **THEN** `validated` is `True`

#### Scenario: CheckConstraintInfo identity

- **WHEN** two `CheckConstraintInfo` instances have the same `schema`, `table_name`, and `name`
- **THEN** they represent the same database constraint
- **AND** `info[:3]` yields that identity

#### Scenario: Payload is an expression, not executable DDL

- **WHEN** a constraint `CHECK (amount >= 0)` on a `numeric` column is inspected
- **THEN** `expression` contains PostgreSQL's deparsed form (e.g. `amount >= 0::numeric`)
- **AND** it does NOT include the `CHECK (...)` wrapper, an `ALTER TABLE` preamble, `NO INHERIT`, or `NOT VALID`

## ADDED Requirements

### Requirement: Check constraint validation state is loaded

`inspect_check_constraints` SHALL read `pg_constraint.convalidated` into `CheckConstraintInfo.validated`.

#### Scenario: Validated constraint

- **WHEN** a constraint was added without `NOT VALID`, or was validated with `VALIDATE CONSTRAINT`
- **THEN** its `validated` field is `True`

#### Scenario: NOT VALID constraint

- **WHEN** a constraint was added with `ALTER TABLE ... ADD CONSTRAINT ... CHECK (...) NOT VALID`
- **THEN** its `validated` field is `False`
- **AND** its `expression` field holds the deparsed expression without the `NOT VALID` suffix
