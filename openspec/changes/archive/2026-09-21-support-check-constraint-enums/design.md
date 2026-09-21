# Design

## Context

See proposal.md for motivation. The comparator in `compare_check_constraints.py` already owns the case where a named
check constraint exists on both sides. It compiles the metadata expression, canonicalizes it through PostgreSQL, and
emits `DropConstraintOp` plus `AddConstraintOp` when the deparsed forms differ. Three facts from the current code and
its dependencies shape this design:

- `_metadata_check_constraints` skips every constraint with `_type_bound`. SQLAlchemy sets that flag on the constraint
  it derives from `Enum(native_enum=False)` and on the one it derives from `Boolean(create_constraint=True)`.
- Alembic's renderer for `CreateCheckConstraintOp` renders the name, table, expression, and schema only. It drops the
  `postgresql_not_valid` entry from `op.kw`, even though `op.to_constraint()` and `op.create_check_constraint()` forward
  it. A migration that must say `NOT VALID` therefore needs a renderer of its own.
- Alembic's `Dispatcher.dispatch()` walks the method resolution order of the operation. A subclass of an Alembic
  operation can register its own renderer, and every other subclass behavior stays inherited.
- Alembic's `checkconstraint_byname` plugin collects its metadata side with `all_table_check_constraints()`, which
  excludes every type-bound constraint. The plugin therefore reports the catalog's copy of an `Enum` constraint as
  removed and emits `DropConstraintOp` for it on every run. It never adds a missing one. Alembic 1.19.2 renamed the
  plugin to `alembic.ext.checkconstraint_byname` and stopped enabling it by default, but did not change that logic.
- `PriorityDispatcher` runs comparators from `DispatchPriority.FIRST` to `LAST`. Within one priority it runs the
  dialect-qualified functions before the default ones, so a `MEDIUM` comparator qualified to `postgresql` runs before
  Alembic's own `MEDIUM` comparators.

## Goals / Non-Goals

**Goals:**

- Compare the constraint that a non-native `Enum` produces, without a new declaration channel
- Emit a changed value set in two revisions: one `NOT VALID` addition, one validation
- Converge on the validation state from the catalog alone, so a missed revision heals on the next run
- Keep every other expression on the current drop and add path

**Non-Goals:**

- The non-goals in proposal.md
- An `op.validate_constraint()` directive. The package renders `op.execute()` for every operation it owns, and
  validation follows that pattern.
- Rendering `postgresql_not_valid` for constraints that Alembic's own comparator adds. That renderer belongs to Alembic.

## Decisions

### D1: Keep a type-bound constraint when SQLAlchemy creates it on PostgreSQL

SQLAlchemy attaches a `_create_rule` to each type-bound constraint. The rule answers one question for a DDL compiler:
does this dialect create the constraint? For `Enum(native_enum=False)` on PostgreSQL the answer is yes. For
`Boolean(create_constraint=True)` the answer is no, because PostgreSQL has a native boolean type. For a native `Enum`
the answer is no, because PostgreSQL creates a `pg_enum` type instead.

`_metadata_check_constraints` evaluates that rule with `dialect.ddl_compiler(dialect, None)` and keeps the constraint
when the rule returns true. The catalog holds exactly the constraints the rule accepts, so the comparator compares
nothing that cannot exist.

**Alternative considered:** test `isinstance(column.type, Enum)` and `native_enum`. Rejected. That check restates the
rule that SQLAlchemy already owns, and it breaks for a `TypeDecorator` that wraps an `Enum`.

### D2: Compile metadata expressions without the table prefix

The `Enum` constraint holds a column expression, not text. The current compile call renders it as
`orders.status IN ('a', 'b')`. SQLAlchemy's DDL compiler renders the same constraint with `include_table=False`, which
gives `status IN ('a', 'b')`. The comparator now passes `include_table=False` too. The compiled text then matches the
form that SQLAlchemy emits and the form that PostgreSQL deparses, so the text short-circuit fires for more constraints.

### D3: Append `validated` to `CheckConstraintInfo` with a default of `True`

The inspector reads `pg_constraint.convalidated` into a new trailing field. The default keeps four-field construction
working, which the existing tests and any caller rely on. Identity becomes `info[:3]`, and the spec scenario that said
`info[:-1]` changes with it. `diff.py` never sees `CheckConstraintInfo`, so nothing else depends on the slice.

### D4: A classifier module that parses with postgast

`value_sets.py` parses an expression by wrapping it as `SELECT <expression>` and reading the first target. It accepts
two shapes: `A_Expr` of kind `AEXPR_IN` with operator `=`, and `A_Expr` of kind `AEXPR_OP_ANY` with operator `=` over an
`A_ArrayExpr`. It unwraps `TypeCast` nodes around the column, the array, and each element, because `pg_get_expr` adds
casts such as `(status)::text` and `'a'::character varying`. Only string constants count as values. Any other shape,
operator, or literal type yields no value set.

The classification compares two value sets on the same column:

| Relation                                    | Result      |
| ------------------------------------------- | ----------- |
| desired is a strict superset of current     | `WIDENING`  |
| desired is a strict subset of current       | `NARROWING` |
| both sides add values and remove values     | `DISJOINT`  |
| columns differ, sets equal, or no value set | `UNKNOWN`   |

A `DISJOINT` change also covers two sets with no value in common. Both cases remove values, and the backfill note treats
them the same way.

**Alternative considered:** regular expressions over the deparsed text. Rejected. The cast forms vary by column type,
and postgast already parses both shapes exactly.

### D5: Emit a subclass of `CreateCheckConstraintOp` for `NOT VALID` additions

`CreateCheckConstraintNotValidOp` extends Alembic's `CreateCheckConstraintOp`. Its constructor forces
`postgresql_not_valid=True` into `kw`, so `to_constraint()` and `reverse()` work unchanged. It carries the column name
and the removed values, so the renderer can write the backfill note. The renderer calls Alembic's renderer for the
parent class, then rewrites the trailing `)` to `, postgresql_not_valid=True)`. The rendered directive is
`op.create_check_constraint(...)` with one extra keyword, and it works inside `batch_alter_table` because the parent
renderer handles the batch prefix.

The comparator builds this operation when the classification is `WIDENING`, `NARROWING`, or `DISJOINT` and the
validation mode is `"deferred"`. It also builds it when the metadata constraint declares `postgresql_not_valid=True`,
whatever the expression, because that flag would otherwise vanish in the rendered migration.

Every add operation the comparator emits, this one and Alembic's plain `CreateCheckConstraintOp`, is built from the
compiled expression text rather than through `from_constraint()`. The `Enum` constraint holds a bound column expression.
When Alembic calls `to_constraint()` on an operation that carries such an expression, to render or reverse it,
SQLAlchemy auto-attaches the new `CheckConstraint` to the column's table, which is the user's metadata table. A second
constraint under the same name, flagged `NOT VALID`, then shadows the declared one on the next run in the same process.
Text has no columns, so nothing attaches, and Alembic renders the same call either way.

**Alternative considered:** replace Alembic's renderer with `replace=True`. Rejected. Importing this package would then
change the output of Alembic's own comparator for every user.

### D6: `ValidateConstraintOp` renders `op.execute()` and reverses to a `NoOp`

`ValidateConstraintOp(constraint_name, table_name, schema=None)` renders
`op.execute('ALTER TABLE "schema"."table" VALIDATE CONSTRAINT "name"')`. The identifiers go through the dialect's
identifier preparer, as `DropViewOp` does.

PostgreSQL offers no statement that marks a validated constraint as not validated. `reverse()` therefore returns
`NoOp(reason)`. `NoOp` renders as `pass` followed by a comment that holds the reason, so a downgrade body stays valid
Python and tells the reader why nothing happens. `NoOp.reverse()` returns the same `NoOp`.

**Alternative considered:** raise in `reverse()`. Rejected. Alembic reverses every operation while it writes the
downgrade, so raising would make every autogenerate run that emits a validation fail.

### D7: The comparator reads validation state on every run

For each shared name whose expression is unchanged, the comparator compares `validated` against the metadata flag. It
emits `ValidateConstraintOp` when the catalog says `false` and the metadata does not declare `postgresql_not_valid`.
This runs in both validation modes. A changed expression never emits a validation, because the drop and add pair
replaces the constraint.

The reflected constraint built for `DropConstraintOp` carries `postgresql_not_valid` from the catalog, so the operation
records the state it removes.

### D8: One configuration key, read from `autogen_context.opts`

`pg_check_constraint_validation` accepts `"deferred"` (default) and `"immediate"`. `"immediate"` keeps the single
validating statement for every change. Any other value raises `ValueError`, because a silent default would hide a
misconfiguration. The key joins the list that `_warn_unrecognized_options` checks, so a misspelling logs a warning that
names the intended key.

### D9: Own type-bound constraints end to end

The division of work in the current design gives existence to Alembic and expression comparison to this package. That
division breaks for a type-bound constraint, because Alembic's plugin never sees one on the metadata side. Two
consequences follow, and the comparator handles both:

- The comparator registers with `priority=DispatchPriority.LAST`, so it runs after Alembic's plugin. It then removes
  every `DropConstraintOp` of type `check` whose name matches a type-bound constraint that exists in both the catalog
  and the model, and logs the removal. Without this step the enum constraint would be dropped on every run whenever the
  plugin is enabled.
- A type-bound constraint that the model declares and the catalog lacks gets `AddConstraintOp`, the same validating
  addition that Alembic emits for any new constraint. SQLAlchemy defaults `create_constraint` to `False`, so a model
  that turns it on for an existing column needs exactly this addition.

A drop that Alembic emits for a type-bound constraint the model no longer declares stays in place. That case is a real
removal, such as `create_constraint=True` changed back to `False`.

**Alternative considered:** return `PriorityDispatchResult.STOP` before Alembic's plugin runs. Rejected. `STOP` only
stops functions in the same subgroup, and the two comparators register different subgroups.

## Risks / Trade-offs

- **[The comparator edits another plugin's operations]** → The removal matches on the operation class, the constraint
  type, and a name the model declares as type-bound. Nothing else is touched. A unit test covers a drop that must stay.

- **[A narrowing leaves invalid rows in place until the backfill]** → The rendered migration carries a comment that
  names the removed values and the constraint. The validation revision fails with a PostgreSQL error that names the
  constraint if the backfill did not run.

- **[Two autogenerate runs per value set change]** → A deployment that runs only the first revision holds a `NOT VALID`
  constraint that PostgreSQL still enforces for new rows. The next autogenerate run emits the validation. The README
  documents the two-revision flow.

- **\[Private SQLAlchemy attributes: `_type_bound` and `_create_rule`\]** → The comparator already reads `_type_bound`,
  and Alembic reads it too. `_create_rule` has the same age. A missing attribute falls through to "skip", which is the
  current behavior.

- **\[The `NOT VALID` renderer edits Alembic's rendered text\]** → The rewrite depends on the rendered call ending with
  `)`. A unit test renders the operation and asserts the exact output, so an Alembic change surfaces at once.

- **[BREAKING: enum constraints compare for the first time]** → A configuration with drifted non-native `Enum` columns
  gets a migration on the next run. That migration is the fix for the drift, and the proposal marks the change as
  breaking.

## Migration Plan

The package has no persistent state. A user upgrades the package, runs autogenerate, and reviews the migration. A user
who prefers the old single-statement behavior sets `pg_check_constraint_validation="immediate"`.
