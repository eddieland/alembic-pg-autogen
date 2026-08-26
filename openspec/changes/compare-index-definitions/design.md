## Context

The library supports functions, triggers, and views through one pipeline it owns end to end, and check constraints
through a second pattern: Alembic owns existence, this library owns whether two same-named objects mean the same thing.
Indexes are a third case, and the difference matters for every decision below.

Alembic has an opinion about indexes, and it is a partial one. `_ix_constraint_sig` compares columns, expressions,
uniqueness, and — through `PostgresqlImpl._dialect_options` — `nulls_not_distinct`. Everything else a PostgreSQL index
can be is absent from the signature: the `WHERE` predicate, the access method, `INCLUDE`, and operator classes. And the
expression comparison it does perform runs through `_cleanup_index_expr`, which lowercases and strips quotes, casts,
sort modifiers, and whitespace before comparing strings.

So unlike check constraints, where `compare_check_constraint()` returns `Equal()` unconditionally and the two
comparators are disjoint by construction, here Alembic sometimes decides an index changed and emits ops for it. Any
design has to say what happens when both comparators look at the same index.

## Goals / Non-Goals

**Goals:**

- Detect changed predicates, access methods, operator classes, `INCLUDE` lists, and expressions on indexes declared in
  `target_metadata`, and emit a drop/create pair
- Normalize through PostgreSQL rather than through string heuristics
- Compose with Alembic's index comparator without ever double-emitting
- Keep the round-trip cheap enough to run on every autogenerate, against a real development database
- Make `CREATE INDEX CONCURRENTLY` expressible in the generated migration

**Non-Goals:**

- A `pg_indexes` DDL-string channel
- Index existence, unnamed indexes, constraint-backed indexes
- Second-guessing an index Alembic already decided about
- Running anything concurrently during autogenerate itself

## Decisions

### D1: Source desired state from `target_metadata`, not from DDL strings

Same reasoning as check constraints. Indexes already live in SQLAlchemy models and Alembic already manages their
existence from there. A parallel DDL channel would need an ownership model — table scoping, metadata-name exclusion,
warnings for indexes declared twice — and every rule in it is a chance to drop an index the user wanted.

Reading from `target_metadata` needs no ownership model: Alembic owns existence, this library owns definition.

**Trade-off:** indexes on tables outside `target_metadata` are not supported. They are equally invisible to Alembic
today.

### D2: Compare a *shape*, not the full `pg_get_indexdef()` output

`pg_get_indexdef()` emits
`CREATE [UNIQUE] INDEX <name> ON <schema>.<table> USING <am> (<keys>) [INCLUDE (...)] [NULLS NOT DISTINCT] [WITH (...)] [WHERE ...]`.
The head is identity — matched separately, by name — and the tail is what this comparator is actually about. `IndexInfo`
therefore carries `(unique, shape)` as its payload, where *shape* is everything from `USING` onward.

Splitting identity off is what makes the desired side comparable at all: it is read back from a different table (D4), so
any comparison that included the table reference would never match.

`UNIQUE` is carried as its own field rather than folded into *shape* because it appears in the head, ahead of the name.
This is the one place `IndexInfo` departs from the "identity is every field but the last" convention the other catalog
types follow, and the docstring says so.

**Splitting is done in SQL, and verified.** The prefix is rebuilt with `quote_ident()` and compared against the
definition's leading characters with `left()`; only on a match is it stripped. Splitting on the first `" USING "`
instead would be wrong for a real if unlikely case — an index named `"ix USING y"` on a table named `"tbl USING x"`
renders a definition whose first `" USING "` sits inside a quoted identifier. An index whose prefix does not match is
dropped from the result with a warning rather than reported with an unstripped shape, because an unstripped shape could
never equal a canonicalized one and would show up as a permanent phantom difference.

### D3: Emit Alembic's own operations for the default path

The comparator appends `ops.DropIndexOp.from_index(reflected)` and `ops.CreateIndexOp.from_index(metadata_index)`.

SQLAlchemy's PostgreSQL reflection is lossless for this purpose — it returns `postgresql_ops`, `postgresql_using`,
`postgresql_where`, `postgresql_include`, and `postgresql_with` — so the reflected index reconstructs a faithful
`op.create_index()` for the downgrade. Generated output is ordinary Alembic:

```python
def upgrade() -> None:
    op.drop_index(op.f("ix_t_a"), table_name="t", postgresql_include=[])
    op.create_index("ix_t_a", "t", ["a"], unique=False, postgresql_where=sa.text("deleted_at IS NULL"))
```

**Alternative considered:** custom ops rendering `op.execute()` with the canonical DDL, matching how this library
handles functions, triggers, and views. Rejected for the reason check constraints rejected it — it would duplicate
operations Alembic already has, produce raw SQL where users expect `op.create_index()`, and bypass `include_object`
filters. That pattern exists because Alembic has no `CreateFunctionOp`; it does have `CreateIndexOp`.

### D4: Probe on an empty `TEMP` clone, not on the real table

For each candidate index the canonicalizer opens a savepoint, creates `TEMP TABLE <table> (LIKE <real table>)`, runs the
metadata index's compiled `CREATE INDEX` against the clone, reads the probe's deparsed definition back, and rolls the
savepoint back.

This is the one place the check-constraint design does not carry over. There, probing the real table was free:
`NOT VALID` skips the validation scan, and the design doc explicitly rejected a clone as trading a guaranteed-identical
deparse context for a very-likely-identical one. `CREATE INDEX` has no `NOT VALID` — it builds the index. Measured on a
500k-row table: 1218 ms for an expression index and 2138 ms for a GIN index, against 0.8 ms each on an empty clone, plus
1.4 ms to create the clone. Autogenerate against a developer's database would stall for seconds per index.

The clone also removes the risk the check-constraint design had to document: probing the real table takes a lock that
PostgreSQL holds until the transaction ends, so a probed table stayed locked for the rest of the autogenerate run.
Nothing here touches the real table beyond reading its shape.

`CREATE TABLE ... (LIKE ...)` copies the column names, types, and collations the deparse depends on, which was verified
to produce byte-identical shapes for expression, predicate, opclass, `INCLUDE`, GIN, and `NULLS NOT DISTINCT` indexes.

**Redirecting the DDL onto the clone** takes two mechanisms together, because either alone leaves a case uncovered. The
clone takes the real table's own name, which catches metadata that leaves its schema implicit — `pg_temp` is searched
first, so an unqualified `ON t` resolves to the clone. Metadata that names its schema compiles to `ON myschema.t` and
would hit the real table, so the DDL executes under `schema_translate_map={None: "pg_temp", schema: "pg_temp"}`. Mapping
to `None` does not work: SQLAlchemy renders the default schema as `public` rather than omitting it.

**Alternative considered:** dropping and recreating the real index inside the savepoint under its own name, which would
make the definitions compare with no splitting at all. Rejected — it pays the full build cost twice and briefly removes
a real index.

### D5: Run last, and defer to Alembic on any index it already touched

Alembic's index comparator registers at `DispatchPriority.MEDIUM`; this one registers at `LAST`, so it runs after
Alembic has populated `modify_table_ops`. Before comparing, it collects the `index_name` of every operation already
there and skips those indexes.

That keeps the two disjoint in the only way available, since they are not disjoint by construction. Alembic owns
existence and owns any index its own comparison rejected; this comparator adds detections only where Alembic was silent.
Testing found no case where Alembic reports a difference for two identical indexes, so deferring to it never suppresses
a real finding.

**Alternative considered:** registering under Alembic's own `"indexes"` subgroup with `qualifier="postgresql"` and
returning `STOP`, which the dispatcher supports and which would replace Alembic's comparison outright. Rejected —
`_compare_indexes_and_uniques` also owns unique constraints and index existence, so stopping it would mean
reimplementing both.

### D6: `CONCURRENTLY` is a rendering opt-in, wrapping Alembic's own rendered call

`pg_index_concurrently=True` makes the comparator wrap each operation in `CreateIndexConcurrentlyOp` /
`DropIndexConcurrentlyOp`. These set `postgresql_concurrently=True` on the operation they wrap and render it through
`render_op()` — Alembic's own renderer — inside `op.get_context().autocommit_block()`:

```python
with op.get_context().autocommit_block():
    op.create_index("ix_t_a", "t", ["a"], postgresql_where=sa.text("..."), postgresql_concurrently=True)
```

The block is required, not decorative: PostgreSQL refuses `CREATE INDEX CONCURRENTLY` inside a transaction block, and
Alembic runs migrations in one. Delegating the body keeps every `postgresql_*` keyword Alembic would have emitted, so
the opt-in contributes only the wrapper.

This affects the *migration*, never autogenerate: the canonicalization probe is always an ordinary `CREATE INDEX` on a
throwaway clone, where concurrency would be pure cost.

### D7: Degrade to "assume unchanged" on any failure

An index that will not compile or will not apply — an expression referencing a column that does not exist yet, an
operator class the access method does not accept — produces a logged warning and no operation. Each probe runs in its
own nested savepoint, so one unusable index does not cost the rest of the table its comparison.

## Risks / Trade-offs

**[The clone's deparse context is a copy, not the original]** → `LIKE` reproduces column names, types, and collations,
but it is a reconstruction. **Mitigation:** verified byte-identical across every supported feature; and a mismatch would
produce a spurious drop/create, which is visible in review, rather than silent drift.

**[Deferring to Alembic inherits its lossy expression comparison]** → when `_cleanup_index_expr` wrongly reports two
expressions equal, Alembic emits nothing and this comparator then catches it; but when Alembic emits ops for the wrong
reason, this comparator stands aside. **Mitigation:** no such false positive was found in testing, and the failure mode
is a redundant migration rather than drift.

**[One savepoint and one clone per table]** → a schema with many indexed tables issues several statements per table.
**Mitigation:** only tables with indexes on both sides are probed at all, and the clone is empty, so the cost is
statement overhead rather than index building.

**\[`CREATE TEMP TABLE` must be permitted\]** → a connection forbidden from creating temporary tables cannot probe.
**Mitigation:** the failure is caught and every index on the table is reported unchanged, per D7.

**[Depends on Alembic's dispatch ordering]** → D5 relies on `DispatchPriority.LAST` running after `MEDIUM`, and on
Alembic's index operations exposing `index_name`. **Mitigation:** both are public API, and the plugin is independently
disableable.

## Open Questions

_(none — all significant decisions resolved above)_
