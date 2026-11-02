# Design

## Context

See proposal.md for the motivation. The comparator in `compare_check_constraints.py` registers with
`DispatchPriority.LAST`, so it runs after Alembic's `checkconstraint_byname` plugin. It already discards that plugin's
drop of a type-bound constraint. These facts shape this design:

- `_metadata_check_constraints` skips each constraint without a name. SQLAlchemy gives the constraint of an `Enum`
  without `name=` the marker `_NONE_NAME`, not `None`. `format_constraint()` resolves that marker to `None` when no
  naming convention applies, and it asserts on a plain `None`.
- `CheckConstraintInfo` carries no OID. `inspect_check_constraints` sorts by schema, table, and constraint name.
- Alembic reverses `create_check_constraint(None, ...)` to `drop_constraint(None, ...)`. That call raises
  `CompileError: ... it has no name`.
- PostgreSQL names an unnamed `CHECK` so that no constraint in the same schema holds the name. Table `y` gets
  `y_a_check1` when table `other` holds `y_a_check`.
- `ALTER TABLE ... ADD CONSTRAINT` takes `ACCESS EXCLUSIVE`, even with `NOT VALID`. An open reader blocks it, and every
  later reader queues behind it.
- A `pyformat` dialect such as psycopg doubles each `%` in compiled SQL. Alembic's renderer compiles the condition of
  `CreateCheckConstraintOp` with the dialect of the connection, so `code LIKE 'A%'` renders as `'A%%'`.
- `text()` reads `:b` in `'a :b'` as a bind parameter. Execution then raises `StatementError`, which is not a
  `DBAPIError`. `op.create_check_constraint()` passes its condition through `text()` at run time.
- Alembic's `add_column` renders a column without its column-level `CheckConstraint`. It does create the constraint that
  a non-native `Enum` type generates.
- Alembic 1.20 renders the dialect keywords of a check constraint add. Earlier releases ignore them.

## Goals / Non-Goals

**Goals:**

- Remove the spurious drop for each unnamed model constraint that the catalog holds
- Add a missing unnamed model constraint with a migration that can be downgraded
- Keep the drop of a catalog constraint that no model constraint matches
- Probe without a lock that blocks readers or writers of the real table

**Non-Goals:**

- Rename detection, renames to a convention name, and offline autogenerate (see proposal.md)
- The duplicate add of a named type-bound constraint on Alembic 1.20
- The `%` doubling in the adds that Alembic's own plugin renders

## Decisions

### D1: An unnamed constraint is one whose name resolves to `None`

`_unnamed_check_constraints` collects each `CheckConstraint` whose name is `None`. It also collects each one whose
`format_constraint()` result is `None`, which covers `_NONE_NAME`. A naming convention that names the constraint moves
it to the named path. Type-bound constraints use the `_created_on_dialect` rule of the named path.

### D2: Candidates are the reflected constraints that no named model constraint uses

A reflected constraint whose name a named metadata constraint uses already has a match. An unnamed model constraint with
the same expression is a second constraint, and it gets an add. A candidate that `include_name` excludes still pairs.
Otherwise its model copy would get a duplicate add. The comparator emits no operation for such a pair.

### D3: Canonicalize with labels, and match text first

The comparator first compares the compiled text with each candidate's deparsed expression, ignoring whitespace. A match
needs no round trip. The other expressions go to `canonicalize_check_constraints` under labels such as
`unnamed check constraint #0 (amount > 0)`. Without candidates, the comparator skips canonicalization.

### D4: Pair copies in metadata order and catalog name order

For each canonical expression, the comparator pairs `min(n, m)` copies. An OID order would need a new field on
`CheckConstraintInfo`, which breaks positional destructuring again. Name order puts `orders_amount_check` before
`orders_amount_check1`, which is creation order in the common case. A count that differs logs a warning.

### D5: A pair discards Alembic's drop and uses the named validation rule

`_discard_drops` removes the `DropConstraintOp` for each paired reflected name. A pair whose catalog row is `NOT VALID`
gets `ValidateConstraintOp`, unless the model declares `postgresql_not_valid=True`.

### D6: Probes run on an empty `TEMP` copy, one savepoint per probe

Both probe functions create `pg_temp.<table>` with `CREATE TEMP TABLE ... (LIKE ...)` inside a savepoint. `LIKE` takes
`ACCESS SHARE` on the real table, which conflicts only with `ACCESS EXCLUSIVE`. The copy has the column names, types,
and collations that `pg_get_expr()` uses, so the canonical form is the same. `canonicalize_indexes` already uses this
pattern. Each probe runs in its own nested savepoint, so a rejected expression affects only its own entry. A connection
without a transaction cannot open the savepoint, and the functions log a warning and return no result.

### D7: `choose_check_constraint_names` asks PostgreSQL, against the real schema's names

The function adds each expression to the copy without a name, in order, and reads back the new name. The copy lives in
`pg_temp`, so PostgreSQL cannot see the constraint names of the real schema. The function reads those names, and it adds
the names in `reserved`. When the copy chooses a taken name, the function rolls back that probe, adds a `CHECK (true)`
placeholder under the name, and probes again. PostgreSQL tries `base`, `base1`, `base2` in order, so the result equals
the name that the real table would get. The comparator passes the names of all named constraints on the metadata table
as `reserved`, because Alembic adds the missing ones in the same migration.

### D8: An unpaired value set on a column is a changed constraint

An unnamed non-native `Enum` whose members change leaves one unpaired constraint on each side. When both parse as value
sets on the same column, the comparator treats them as one changed constraint. It discards Alembic's drop and emits its
own `DropCheckConstraintOp` and `_add_constraint_op` under the reflected name. A narrowing is therefore added
`NOT VALID`, as for a named `Enum`. The next run pairs the model constraint with the new catalog row and validates it.

### D9: A constraint whose probe fails keeps the leftover drops

With candidates, a constraint whose canonicalization failed may match a leftover candidate. Dropping that candidate
would remove a rule the model still declares. The comparator therefore discards Alembic's drops of all leftover
candidates and logs a warning. A text constraint on a column that the same migration adds fails its probes, so it gets
its add on the next run. The comparator logs a warning for it.

Alembic's `create_table` renders a new table's columns without their column-level `CheckConstraint`. The comparator
skips a table that only the model holds, so it logs a warning for each unnamed column-level constraint on such a table.
The next run adds the constraint. A constraint that a type generates is not affected, because the column's type creates
it with the column.

### D10: Text ops render their condition exactly

The compiled text undoes the `%` doubling of a `pyformat` dialect. `CreateCheckConstraintTextOp` escapes each colon that
`text()` would read as a bind parameter, and its renderer puts the op's own text in place of Alembic's compiled form.
`DropCheckConstraintOp` keeps the catalog expression and reverses to that op, so a downgrade restores the expression
exactly. Every add and drop that this comparator emits uses these classes. The add does not carry
`postgresql_not_valid`, because the op class encodes the flag. The `NOT VALID` renderer appends the keyword only when
Alembic did not render it.

## Risks / Trade-offs

- **Two migrations for a new column or table.** An unnamed constraint on a new column, or a column-level one on a new
  table, needs a second run. A warning names it.
- **Kept drops.** A failed probe can delay a correct drop by one run. The warning names the constraints.
- **Names depend on the catalog at autogenerate time.** A named constraint added to another table in the same migration
  is not reserved. A collision makes the upgrade fail with a clear error.
- **No `TEMP` table.** A read-only transaction cannot create the copy. The comparison then degrades with a warning.
- **New add operations.** A model whose unnamed constraints are missing gets adds for the first time.

## Migration Plan

No configuration changes. A project with unnamed constraints sees fewer drops on the next run. It may see adds for
unnamed constraints that the database lacks.
