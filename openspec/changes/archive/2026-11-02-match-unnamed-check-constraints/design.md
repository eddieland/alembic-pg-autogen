# Design

## Context

See proposal.md for the motivation. The comparator in `compare_check_constraints.py` registers with
`DispatchPriority.LAST`, so it runs after Alembic's `checkconstraint_byname` plugin. It already discards that plugin's
drop of a type-bound constraint. Four facts shape this design:

- `_metadata_check_constraints` skips each constraint without a name. SQLAlchemy gives the constraint of an `Enum`
  without `name=` the marker `_NONE_NAME`, not `None`. `format_constraint()` resolves that marker to `None` when no
  naming convention applies, and it asserts on a plain `None`.
- `CheckConstraintInfo` carries no OID. `inspect_check_constraints` sorts by schema, table, and constraint name.
- Alembic reverses `create_check_constraint(None, ...)` to `drop_constraint(None, ...)`. That call raises
  `CompileError: ... it has no name`.
- Alembic's `add_column` renders a column without its column-level `CheckConstraint`. It does create the constraint that
  a non-native `Enum` type generates.

## Goals / Non-Goals

**Goals:**

- Remove the spurious drop for each unnamed model constraint that the catalog holds
- Add a missing unnamed model constraint with a migration that can be downgraded
- Keep the drop of a catalog constraint that no model constraint matches

**Non-Goals:**

- Rename detection, renames to a convention name, and offline autogenerate (see proposal.md)
- Changes to `canonicalize_check_constraints`, whose batch failure behavior the named path relies on
- The duplicate add of a named type-bound constraint on Alembic 1.20

## Decisions

### D1: An unnamed constraint is one whose name resolves to `None`

`_unnamed_check_constraints` collects each `CheckConstraint` whose name is `None`. It also collects each one whose
`format_constraint()` result is `None`, which covers `_NONE_NAME`. A naming convention that names the constraint moves
it to the named path. Type-bound constraints use the `_created_on_dialect` rule of the named path. The function keeps
declaration order, table constraints first and column constraints after.

### D2: Only reflected constraints that no named model constraint uses can pair

A reflected constraint whose name a named metadata constraint uses already has a match. An unnamed model constraint with
the same expression is a second constraint, and it gets an add. Reflected names also pass `include_name` before they can
pair, as in Alembic's plugin.

### D3: Canonicalize with labels, and match text first

The comparator first compares the compiled text with each candidate's deparsed expression, ignoring whitespace. A match
needs no round trip. The other expressions go to `canonicalize_check_constraints` under labels such as
`unnamed check constraint #0 (amount > 0)`, because the function keys its result by name. The function already accepts
any key. Without candidates, the comparator skips canonicalization, because nothing can pair.

### D4: Pair copies in metadata order and catalog name order

For each canonical expression, the comparator pairs `min(n, m)` copies. Metadata copies keep declaration order. Catalog
copies keep the order of `inspect_check_constraints`, which is name order. The proposal named OID order. An OID would
need a new field on `CheckConstraintInfo`, which breaks positional destructuring again. Name order puts
`orders_amount_check` before `orders_amount_check1`, which is creation order in the common case. A count that differs
logs a warning.

### D5: A pair discards Alembic's drop and uses the named validation rule

`_discard_drops` removes the `DropConstraintOp` for each paired reflected name. Its log message no longer says
"type-bound". A pair whose catalog row is `NOT VALID` gets `ValidateConstraintOp` under the reflected name, unless the
model declares `postgresql_not_valid=True`. `include_object` receives the metadata constraint and the reflected name.

### D6: `choose_check_constraint_names` asks PostgreSQL for the name of each add

The new public function adds each expression without a name, in order, inside one savepoint. Each add runs in its own
nested savepoint. After each add, the function reads the constraint names of the table and takes the one new name. An
expression that PostgreSQL rejects gives `None` and rolls back only its nested savepoint. The outer savepoint is always
rolled back. The comparator renders each add with the chosen name, so the downgrade can drop it.

Sequential adds matter. A second `amount` constraint sees the first one and gets `orders_amount_check1`. The migration
adds the constraints in the same order, so the names agree.

### D7: A constraint whose probe fails is left alone

With candidates, a constraint whose canonicalization failed may still match one of them. The comparator then emits no
add, and Alembic's drop of the candidate stays. That is the current behavior. Without candidates, a failed name probe
also emits nothing. A text constraint on a column that the same migration adds fails both probes. It gets its add on the
next run.

## Risks / Trade-offs

- **Two migrations for a new column.** A column-level unnamed constraint on a new column needs a second autogenerate
  run. A fix would need the probe to run after the planned `add_column`. That is out of scope.
- **Batch canonicalization.** One rejected expression in `canonicalize_check_constraints` fails all probes for that
  table. The pairs then fall back to the current behavior for one run, with a warning.
- **Names depend on the catalog at autogenerate time.** The migration renders a fixed name. A constraint added to the
  database between autogenerate and upgrade can collide with it. The upgrade then fails with a clear error.
- **New add operations.** A model whose unnamed constraints are missing from the database gets adds for the first time.
  `include_object` can exclude them.

## Migration Plan

No configuration changes. A project with unnamed constraints sees fewer drops on the next run. It may see adds for
unnamed constraints that the database lacks.
