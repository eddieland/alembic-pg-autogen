## Why

Alembic's `checkconstraint_byname` plugin matches `CHECK` constraints by name only. Since Alembic 1.19.2, the plugin is
opt-in as `alembic.ext.checkconstraint_byname`. The plugin skips each metadata constraint that has no name. PostgreSQL
gives a name to every constraint. The plugin therefore reports each unnamed model constraint as removed, on every run.

These facts are measured against PostgreSQL 16 with Alembic 1.19.1 and 1.20.1. The test model declares four unnamed
constraints on `orders`:

```python
Column("amount", Integer, CheckConstraint("amount > 0"))
Column("status", Enum("a", "b", native_enum=False, create_constraint=True))
CheckConstraint("qty >= 0")
CheckConstraint("qty <= amount")
```

`create_all()` creates `orders_amount_check`, `orders_status_check`, `orders_qty_check`, and `orders_check`. A fifth
constraint, `orders_id_check`, comes from raw SQL. Autogenerate emits `drop_constraint` for all five constraints on both
Alembic versions. Only the fifth drop is correct.

The usual answer is to name every constraint. For a schema in production, that means one rename migration per
constraint. A prediction of the PostgreSQL name from the model is not reliable. PostgreSQL uses `<table>_<column>_check`
for one column and `<table>_check` for zero or several columns. It appends `1`, `2`, and so on after a collision. It
truncates names at 63 bytes.

This package already compares `CHECK` expressions in the canonical form that PostgreSQL produces. It does this only for
names on both sides. The same comparison can match an unnamed model constraint to its catalog constraint.

## What Changes

- Collect the unnamed `CHECK` constraints of each metadata table. Include a type-bound constraint only when SQLAlchemy
  creates it on PostgreSQL, as `_created_on_dialect` decides today.
- Collect the reflected `CHECK` constraints whose name no named metadata constraint uses.
- Canonicalize the unnamed metadata expressions with `canonicalize_check_constraints`, in the existing savepoint.
- Pair each unnamed metadata constraint with a reflected constraint that has an equal canonical expression.
- For a pair, discard the `DropConstraintOp` that Alembic's plugin emitted for the reflected name. Apply the validation
  rule that named pairs use: emit `ValidateConstraintOp` for a `NOT VALID` catalog constraint, unless the model declares
  `postgresql_not_valid=True`.
- For an unnamed metadata constraint with no pair, emit an add. Alembic's plugin ignores this case.
- For a reflected constraint with no pair, emit nothing. Alembic's plugin keeps its drop. A constraint from raw SQL
  therefore still gets a drop.

**Duplicate expressions.** An expression can occur `n` times in the model and `m` times in the catalog. PostgreSQL
accepts two equal expressions on one table (`t1_a_check`, `t1_a_check1`). The comparator pairs `min(n, m)` copies in
catalog creation order (`pg_constraint.oid`). It treats the rest as unpaired and logs a warning with both counts. The
issue proposes a fallback to the current behavior. That behavior drops every catalog copy, which is the defect this
change removes.

**The name of an add.** The add must carry a name. Alembic reverses `create_check_constraint(None, ...)` to
`drop_constraint(None, ...)`, and that call raises `CompileError: ... it has no name`. The probe for an unpaired
constraint therefore runs without a name. The comparator reads back the name that PostgreSQL chose, for example
`orders_qty_check1`. The add renders that name, so the downgrade can drop the constraint.

**Type-bound constraints.** This answers the open question of issue #47. A non-native `Enum` constraint takes part, as
the `orders_status_check` example shows. A `Boolean` constraint does not take part. SQLAlchemy does not create it on
PostgreSQL, so the catalog never holds it.

## Non-goals

- **Rename detection.** A named model constraint and a catalog constraint with another name do not pair, even with equal
  expressions.
- **Renaming catalog constraints.** The package does not emit a rename to the PostgreSQL name or to a convention name.
- **Constraints that a naming convention names.** These constraints have a name, so the current named path handles them.
- **Offline autogenerate.** Canonicalization needs a connection. Offline runs keep the current behavior.
- **Drops without Alembic's plugin.** When the user does not enable `checkconstraint_byname`, this package emits no drop
  for an unpaired catalog constraint.

## Capabilities

### Modified Capabilities

- `check-constraint-comparison`: pair unnamed constraints by canonical expression, discard their drops, and add unpaired
  ones under the name that PostgreSQL chooses. This replaces the scenario "Unnamed constraints are ignored".
- `canonicalization`: canonicalize expressions that have no constraint name, and report the name PostgreSQL chooses.

## Impact

- **Behavior**: **BREAKING** for a model with unnamed `CHECK` constraints. Spurious drops stop. A missing unnamed
  constraint gets an add for the first time.
- **Config**: no new key. The existing filters apply. A pair passes the reflected name to `include_name`.
- **Cost**: one probe per unnamed metadata constraint, in the savepoint that named constraints already use.
- **Public API**: `canonicalize_check_constraints` gains support for unnamed input. The design document decides the
  signature.
- **Related defect**: on Alembic 1.20.1, a missing named non-native `Enum` constraint gets two identical
  `create_check_constraint` calls. Alembic's plugin now includes type-bound constraints, and this package adds them as
  well. That fix is a separate change.
