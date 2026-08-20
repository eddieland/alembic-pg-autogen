## Context

The library supports three PostgreSQL object types — functions, triggers, and views — through one pipeline: inspect the
catalog, canonicalize the desired DDL by round-tripping it through PostgreSQL, diff the two snapshots, and render
`op.execute()` calls. Every layer assumes the library owns the object type end to end, because Alembic has no opinion
about PL/pgSQL functions.

Check constraints are different: Alembic 1.19 added `alembic.autogenerate.checkconstraint_byname`, which compares named
check constraints from `target_metadata` against reflection and emits add/drop ops. What it cannot do is decide whether
two same-named constraints mean the same thing — `DefaultImpl.compare_check_constraint()` returns `Equal()`
unconditionally, and PostgreSQL's impl inherits it. So the object type is half-supported by Alembic and the missing half
is exactly the part that needs a real PostgreSQL parser.

## Goals / Non-Goals

**Goals:**

- Detect changed `CHECK` expressions on constraints declared in `target_metadata` and emit a drop/add pair
- Normalize through PostgreSQL rather than through string heuristics
- Compose with Alembic's own check constraint comparator instead of competing with it
- Keep the round-trip cheap enough to run on every autogenerate

**Non-Goals:**

- A `pg_check_constraints` DDL-string channel (see D1)
- Unnamed, type-bound, domain, or `NOT NULL` constraints
- Comparing or emitting validation state (`NOT VALID`, `VALIDATE CONSTRAINT`)
- Constraints on tables absent from `target_metadata`

## Decisions

### D1: Source desired state from `target_metadata`, not from DDL strings

Functions, triggers, and views are declared to this library as DDL strings because nothing else declares them. Check
constraints already live in SQLAlchemy models, and Alembic already manages their existence from there.

A parallel `pg_check_constraints` option would mean two systems with overlapping claims on the same catalog objects:
Alembic would emit a drop for any constraint present in the database but missing from metadata, including every
constraint declared only to us, while we would emit a drop for every metadata-declared constraint missing from our own
list. Resolving that needs an ownership model — table scoping, metadata-name exclusion, warnings for constraints
declared twice — and every one of those rules is a chance to drop a constraint the user wanted.

Reading from `target_metadata` needs no ownership model at all: Alembic owns existence (add/remove), this library owns
correctness (changed expression). The two never emit an op for the same finding, because Alembic's comparison of a
shared name always returns `Equal()`.

**Trade-off:** check constraints on tables outside `target_metadata` are not supported. That is the price of not
fighting Alembic over ownership, and those constraints are equally invisible to Alembic today.

### D2: Emit Alembic's own operations, add no renderers

The comparator appends `ops.DropConstraintOp.from_constraint(...)` and `ops.AddConstraintOp.from_constraint(...)` — the
same pair Alembic's comparator appends when its own comparison reports a difference.

Building the drop op with `from_constraint()` (rather than constructing `DropConstraintOp` directly) is what makes the
migration reversible: the op carries the reflected constraint, so `downgrade()` renders a `create_check_constraint()`
restoring the catalog's expression. Generated output is ordinary Alembic:

```python
def upgrade() -> None:
    op.drop_constraint("ck_orders_amount", "orders", type_="check")
    op.create_check_constraint("ck_orders_amount", "orders", "amount > 0")


def downgrade() -> None:
    op.drop_constraint("ck_orders_amount", "orders", type_="check")
    op.create_check_constraint("ck_orders_amount", "orders", "amount >= 0::numeric")
```

**Alternative considered:** `AddCheckConstraintOp` / `ReplaceCheckConstraintOp` / `DropCheckConstraintOp` rendering
`op.execute()`, matching the other object types. Rejected — it would duplicate operations Alembic already has, produce
raw SQL where users expect `op.create_check_constraint()`, and bypass `include_object` filters and batch mode.

### D3: Normalize with `pg_get_expr(conbin, conrelid, true)`, not `pg_get_constraintdef()`

Both sides of the comparison read the deparsed expression tree, so both are formatted by the same PostgreSQL routine and
compare as plain strings. `pg_get_expr()` returns the expression alone — no `CHECK (...)` wrapper, no `NO INHERIT`, and
no `NOT VALID` suffix, which matters because the canonicalization probe adds its constraints `NOT VALID` (D4).

This is why `CheckConstraintInfo`'s payload field is named `expression` rather than `definition`: it is not executable
DDL, and it does not need to be, because this library never renders it (D2). The identity convention still holds —
identity is every field but the last.

### D4: Canonicalize on the real table with `NOT VALID` probes

For each candidate constraint the canonicalizer opens a savepoint, runs
`ALTER TABLE <table> ADD CONSTRAINT _alembic_pg_autogen_probe_<n> CHECK (<metadata expression>) NOT VALID`, reads the
probe's deparsed expression back, and rolls the savepoint back.

Adding the probe to the real table guarantees the deparse context — column names, types, collations, domains — matches
the constraint it is being compared against, so any difference in the result is a real difference in the constraint.

`NOT VALID` is what keeps this affordable: PostgreSQL skips the full-table validation scan, so the probe costs no row
reads no matter how large the table, and rows that violate a newly tightened constraint do not turn autogenerate into an
error. Probe names are prefixed and rolled back, so nothing survives the savepoint.

**Alternative considered:** probing a `CREATE TEMP TABLE (LIKE ...)` clone, which would avoid locking the real table.
Rejected for now — it trades a guaranteed-identical deparse context for a very-likely-identical one, and the payoff is
lock behavior during a developer-run command. Worth revisiting if autogenerate against busy databases becomes a concern.

**Alternative considered:** dropping and re-adding the existing constraint inside the savepoint instead of using a probe
name. Rejected — same result, but it briefly removes a real constraint and needs the drop to be undone correctly.

### D5: Skip the round-trip when the text already matches

Before probing, the comparator compares the compiled metadata expression against the catalog expression modulo
whitespace. When a user pastes PostgreSQL's own normalized form into their model — which is what happens after the first
migration cycle — this skips the DDL entirely. The check is sound in one direction only: equal text means equal
constraint, unequal text means "ask PostgreSQL".

### D6: Register as a separate plugin, qualified to PostgreSQL

`alembic_pg_autogen.checkconstraints` is registered as its own plugin so it can be excluded
(`~alembic_pg_autogen. checkconstraints`) without losing function, trigger, and view support, mirroring how Alembic made
its own check constraint comparator independently disableable. It registers with `qualifier="postgresql"`, so it never
fires on another dialect.

### D7: Degrade to "assume unchanged" on any failure

A metadata expression that will not compile, will not apply, or references a column that does not exist yet produces a
logged warning and no operation. Autogenerate reporting one constraint as unchanged is a much smaller failure than
autogenerate raising, and it matches how Alembic handles a comparison it cannot make.

## Risks / Trade-offs

**\[The probe takes `ACCESS EXCLUSIVE` on the table\]** → PostgreSQL holds locks acquired inside a savepoint until the
transaction ends, so a probed table stays locked for the rest of the autogenerate run. **Mitigation:** `NOT VALID` makes
the lock acquisition brief, only tables with metadata check constraints whose text differs are probed at all, and
autogenerate is a developer command run against development databases. Documented in the quick start.

**[One savepoint per table, not per run]** → a schema with many constrained tables issues several statements per table.
**Mitigation:** D5 skips already-canonical constraints entirely, and only shared names are candidates. A schema-level
pre-pass could batch every table into one savepoint if this becomes measurable.

**\[Constraints outside `target_metadata` are unmanaged\]** → users with check constraints on unmanaged tables get
nothing. **Mitigation:** documented; D1 explains why the alternative is worse.

**[Depends on Alembic's plugin API surface]** → the comparator relies on the table-level dispatch signature and on
`DefaultImpl.compare_check_constraint()` continuing to report shared names as equal. If a future Alembic compares
expressions itself on PostgreSQL, both would emit the same drop/add pair. **Mitigation:** the floor is pinned at
`>=1.19` and the plugin is independently disableable.

## Open Questions

_(none — all significant decisions resolved above)_
