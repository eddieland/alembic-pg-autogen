## Why

Alembic's `checkconstraint_byname` plugin matches `CHECK` constraints by name only. Since Alembic 1.19.2, it is opt-in
as `alembic.ext.checkconstraint_byname`. It skips each unnamed metadata constraint. PostgreSQL names every constraint,
so the plugin reports each unnamed model constraint as removed on every run.

On PostgreSQL 16 with Alembic 1.19.1 and 1.20.1, a model with four unnamed constraints and a fifth from raw SQL gets
`drop_constraint` for all five. Only the fifth drop is correct.

Naming every constraint costs one rename migration per constraint in production. A prediction of the PostgreSQL name is
not reliable. PostgreSQL uses `<table>_<column>_check` or `<table>_check`. It appends a number while any constraint in
the schema holds the name, and it truncates at 63 bytes.

## What Changes

- Pair each unnamed metadata `CHECK` with a reflected `CHECK` whose canonical expression is equal. A reflected
  constraint that a named model constraint uses cannot pair.
- For a pair, discard Alembic's drop. Emit `VALIDATE CONSTRAINT` for a `NOT VALID` pair, as for a named pair.
- Treat an unpaired model value set and an unpaired reflected value set on the same column as one changed constraint. It
  gets the drop and add of a named change, so an `Enum` narrowing is added `NOT VALID`.
- Add each other unpaired model constraint under the name that PostgreSQL chooses. The downgrade needs that name,
  because `drop_constraint(None, ...)` raises `CompileError`.
- Keep Alembic's drop for a reflected constraint without a pair.
- Pair duplicate expressions in metadata order and catalog name order. Log a warning when the counts differ.

A non-native `Enum` constraint takes part. A `Boolean` constraint does not, because PostgreSQL never creates it.

## Non-goals

- **Rename detection.** A named model constraint never pairs with another name.
- **Constraints that a naming convention names.** The named path handles them.
- **Offline autogenerate.**
- **Drops without Alembic's plugin.** This package emits no drop for an unpaired catalog constraint.

## Capabilities

### Modified Capabilities

- `check-constraint-comparison`: pair unnamed constraints by canonical expression. This replaces the scenario "Unnamed
  constraints are ignored".
- `canonicalization`: probe on a `TEMP` copy of the table, and choose names for unnamed constraints.

## Impact

- **Behavior**: **BREAKING** for a model with unnamed `CHECK` constraints. Spurious drops stop. A missing unnamed
  constraint gets an add.
- **Config**: no new key. The filters apply. A reflected constraint that `include_name` excludes still pairs, so it gets
  no duplicate add.
- **Cost**: up to two probe rounds per table, one to canonicalize and one to choose names. Each probe runs in its own
  savepoint on an empty `TEMP` copy. The copy takes `ACCESS SHARE` on the real table, so readers and writers do not
  wait. The named path moves to the copy too. On `main` it alters the real table under `ACCESS EXCLUSIVE`.
- **Public API**: new `choose_check_constraint_names`, `CreateCheckConstraintTextOp`, and `DropCheckConstraintOp`.
  `canonicalize_check_constraints` keeps its signature.
- **Related defect**: on Alembic 1.20.1, a missing named non-native `Enum` constraint gets two identical adds. That fix
  is a separate change.
