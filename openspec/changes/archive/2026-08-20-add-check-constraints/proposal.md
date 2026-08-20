## Why

Alembic 1.19 detects when a named `CHECK` constraint is added to or removed from your models, but two constraints that
share a name are always presumed equivalent — the docs are explicit that normalizing SQL expressions is not something
Alembic can do in a backend-agnostic way. So changing `CHECK (amount >= 0)` to `CHECK (amount > 0)` in a model produces
no migration at all, and the schema silently drifts.

This library already knows how to normalize PostgreSQL DDL: round-trip it through the server and read back the catalog's
own form. The same trick closes Alembic's correctness gap for check constraints, which is the one PostgreSQL object type
it can only half-support on its own.

## What Changes

- Add `CheckConstraintInfo` and `inspect_check_constraints()` to the catalog inspector, reading normalized expressions
  via `pg_get_expr(conbin, conrelid, true)`
- Add `canonicalize_check_constraints()`, which normalizes desired expressions by adding them to the live table as
  throwaway `NOT VALID` constraints inside a rolled-back savepoint
- Add a table-level comparator that compares the two and emits Alembic's own `DropConstraintOp` / `AddConstraintOp` pair
  when a shared constraint's expression differs
- Register that comparator as its own plugin, `alembic_pg_autogen.checkconstraints`, so it can be disabled without
  disabling function/trigger/view support
- Extract `current_schema()` into the inspector, replacing the private helper in the comparator module
- Raise the Alembic floor to `>=1.19`, the release that made check constraints part of default autogenerate
- Let the test suite run against an existing PostgreSQL via `ALEMBIC_PG_AUTOGEN_TEST_URL` instead of requiring Docker

**Key pattern difference**: check constraints are the first object type this library does not render itself. Existence
is Alembic's job and always was; only the expression comparison needs PostgreSQL. So there are no new `MigrateOperation`
subclasses and no new renderers — the comparator hands Alembic the ops it already knows how to render, which keeps
`op.create_check_constraint()` output, `include_object` filters, and downgrade reversibility working as users expect.

## Non-goals

- **Constraints outside `target_metadata`** — a check constraint on a table Alembic does not manage is untouched; there
  is no `pg_check_constraints` DDL-string channel, and adding one would put this library in a fight with Alembic's own
  comparator over who owns a constraint
- **Unnamed constraints** — they cannot be matched between metadata and catalog by name, matching Alembic's own
  restriction
- **Type-bound constraints** — the ones SQLAlchemy generates for `Enum(native_enum=False)` and friends are generated,
  not authored, and stay Alembic's
- **Validation state** — `NOT VALID` / `VALIDATE CONSTRAINT` is not expressible in SQLAlchemy metadata, so it is neither
  compared nor emitted
- **Domain constraints and `NOT NULL` constraints** — different catalog shape, different lifecycle

## Capabilities

### New Capabilities

- `check-constraint-comparison`: expression-level comparison of metadata check constraints against the live catalog

### Modified Capabilities

- `catalog-inspector`: add `CheckConstraintInfo`, `inspect_check_constraints()`, and `current_schema()`
- `canonicalization`: add `canonicalize_check_constraints()`

## Impact

- **Config**: none — the comparator activates on its own for PostgreSQL when `target_metadata` has named check
  constraints
- **Behavior**: a changed check constraint expression now produces a migration where it previously produced silence
- **Dependencies**: `alembic>=1.19` (was `>=1.18`)
- **Public API**: new exports — `CheckConstraintInfo`, `inspect_check_constraints`, `canonicalize_check_constraints`,
  `current_schema`
