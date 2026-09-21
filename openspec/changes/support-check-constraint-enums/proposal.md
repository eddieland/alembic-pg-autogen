## Why

Teams leave native PostgreSQL enums because enums resist expand-contract deployment. Two limits drive that decision.
Both are measured against PostgreSQL 16.13.

Removing an enum value rewrites the whole table. The test table holds 1M rows, 17 columns, and 2 indexes. The rewrite
takes 1,892 ms under `ACCESS EXCLUSIVE`. It writes 228 MB of WAL. PostgreSQL prices the rewrite per table, not per
column. Rewriting all six enum columns in one statement costs 2,540 ms and the same 228 MB. One volatile enum column
therefore taxes every other column in the table.

A new enum value is also unusable inside the transaction that adds it:

```
BEGIN;
ALTER TYPE st ADD VALUE 'c';
INSERT INTO tx VALUES (2, 'c');
ERROR:  unsafe use of new value "c" of enum type st
HINT:  New enum values must be committed before they can be used.
```

One Alembic revision therefore cannot add a value and backfill the rows that use it.

SQLAlchemy already ships the replacement. `Enum(Status, native_enum=False, create_constraint=True)` produces a named
`CheckConstraint` over the same value set. This package already compares check constraint expressions against
PostgreSQL. Two gaps stop the pieces from working together.

**The comparator skips the constraint.** `_metadata_check_constraints` skips every constraint that carries
`_type_bound`, and SQLAlchemy marks non-native `Enum` constraints that way. A changed Python enum member produces no
migration, and the schema drifts. Alembic skips these constraints because `Boolean` generates them on backends without a
native boolean type. PostgreSQL has a native boolean type, so that reason does not apply here.

**The emitted migration holds the wrong lock.** The comparator emits `DropConstraintOp` then `AddConstraintOp`. On the
same table that costs 184 ms under `ACCESS EXCLUSIVE`, and the cost grows with row count. The same change as `NOT VALID`
costs 1 ms under `ACCESS EXCLUSIVE`. A separate `VALIDATE CONSTRAINT` then costs 197 ms under `SHARE UPDATE EXCLUSIVE`.
`SHARE UPDATE EXCLUSIVE` blocks no reads and no writes.

## What Changes

- Keep constraints that SQLAlchemy derives from a non-native `Enum`, instead of skipping every `_type_bound` constraint
- Add `validated` to `CheckConstraintInfo`, read from `pg_constraint.convalidated`
- Add a classifier that parses both expressions with postgast and reports `WIDENING`, `NARROWING`, `DISJOINT`, or
  `UNKNOWN`
- Emit a changed value set as `create_check_constraint(..., postgresql_not_valid=True)`
- Add `ValidateConstraintOp` and a renderer for it, emitted whenever the catalog holds a matching constraint with
  `convalidated = false`
- Render a comment on a `NARROWING` or `DISJOINT` change, stating that rows holding a removed value need a backfill
  before the validation revision
- Keep the current drop and add path for every expression that is not a value set

Three facts carry the design. `pg_get_expr(conbin, conrelid, true)` deparses `status IN ('a','b')` to
`status = ANY (ARRAY['a'::text, 'b'::text])`. postgast parses that form and the `IN` form into their literal sets.
SQLAlchemy renders `postgresql_not_valid=True` as `NOT VALID`, and Alembic's `CreateCheckConstraintOp` forwards `**kw`
to `CheckConstraint`, so the flag reaches the DDL. Alembic defines no validate operation, so this change adds one.

A changed value set spans two revisions. The first revision adds the new constraint, which PostgreSQL enforces for new
rows at once. The second revision validates it. The comparator converges on its own, because it reads `convalidated` on
every run. Whether `"deferred"` is the right default is the open question for the design document.

## Non-goals

- **Native enum comparison.** Value set drift inside a `pg_enum` type stays undetected. A mixed schema needs that too,
  and it is a separate change.
- **Migrating a user schema off a native enum type.** The package emits no conversion.
- **Classifying arbitrary expressions.** Only `col IN (...)` and `col = ANY (ARRAY[...])` classify. Every other shape
  keeps the current behavior.
- **Writing the backfill for a narrowing.** The package states the need. The user writes the statement.
- **`autocommit_block()`.** Alembic runs one transaction for a whole upgrade by default. An autocommit block inside the
  third revision commits the first three. A failure in the fifth revision then leaves the database partly migrated.
- **The lookup table pattern.** Separate change. It depends on the module pattern from `generate-audit-tables`.

## Capabilities

### New Capabilities

- `value-set-classification`: classification of a check constraint change as widening, narrowing, or disjoint

### Modified Capabilities

- `check-constraint-comparison`: compare non-native `Enum` constraints, emit `NOT VALID`, emit a deferred validation
- `catalog-inspector`: add `validated` to `CheckConstraintInfo`
- `alembic-operations`: add `ValidateConstraintOp`
- `alembic-render`: add the `ValidateConstraintOp` renderer

## Impact

- **Config**: one new key, `pg_check_constraint_validation`, accepting `"deferred"` (default) or `"immediate"`. The
  `"immediate"` value keeps the current single validating statement.
- **Behavior**: **BREAKING** for a configuration that declares non-native `Enum` columns. This package compares those
  constraints for the first time. The next run may emit a migration that closes existing drift.
- **Types**: `CheckConstraintInfo` gains an appended field. **BREAKING** for positional destructuring.
- **Downgrade**: `ValidateConstraintOp` reverses to nothing. PostgreSQL offers no statement that marks a validated
  constraint unvalidated.
- **Dependencies**: none new. postgast already parses both expression forms.
- **Public API**: new exports for `ValidateConstraintOp` and the classifier.
