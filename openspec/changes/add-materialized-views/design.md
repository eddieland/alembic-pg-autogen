## Context

The library manages functions, triggers, views, and check constraints across four layers: inspection (`inspect.py`),
canonicalization (`canonicalize.py`), diffing (`diff.py`), and Alembic integration (`compare.py`, `ops.py`,
`render.py`). See `proposal.md` for motivation.

Every decision below rests on prototype work against PostgreSQL 16.13 and postgast 0.0.x (2026-08-27). The prototype
script is committed next to this document as `prototype.py`. It proved these facts:

1. `pg_get_viewdef(oid, true)` works for `relkind = 'm'`. The existing view inspection query extends directly.
1. `CREATE OR REPLACE MATERIALIZED VIEW` and `ALTER MATERIALIZED VIEW ... AS` are syntax errors.
1. `CREATE MATERIALIZED VIEW IF NOT EXISTS` keeps the old definition silently when the object exists.
1. `DROP` + `CREATE ... WITH NO DATA` inside a savepoint reads back a canonical definition. Rollback restores the
   original object, its indexes, and its `relispopulated` state.
1. `WITH NO DATA` skips query execution. A query calling a raising function still creates successfully.
1. The canonical definition is byte-identical across formatting variants and across `WITH DATA` / `WITH NO DATA`.
1. `DROP MATERIALIZED VIEW` drops the indexes on the object silently. Replaying `pg_get_indexdef()` output against the
   re-created object restores them. `REFRESH ... CONCURRENTLY` fails without a unique index.
1. postgast parses `CREATE MATERIALIZED VIEW` as `CreateTableAsStmt` with `objtype = OBJECT_MATVIEW`.
   `ensure_or_replace()` passes the DDL through unchanged. `to_drop()` raises `ValueError`. Setting `into.skip_data` and
   calling `postgast.deparse()` emits the statement with `WITH NO DATA`.
1. A regular view can select from a materialized view. Triggers on materialized views are rejected.
1. Creating an index on an unpopulated materialized view works. `REFRESH` then populates it.

## Goals / Non-Goals

**Goals:**

- Add materialized view support across all six layers, following the view patterns
- Keep autogenerate side-effect free and cheap: never execute the view query during comparison
- Preserve indexes across replacement, because losing them breaks `REFRESH ... CONCURRENTLY`

**Non-Goals:**

- Index diffing or declared desired-state indexes (preservation only; a later change can add declaration)
- Refresh scheduling, `REFRESH` emission, or population-state comparison
- Dependency graph analysis; a regular view over a managed materialized view stays unsupported
- Storage options (tablespace, access method, storage parameters); they do not round-trip through `pg_get_viewdef()`
- Backfilling the extension-ownership filter for regular views (`inspect_views` lacks it today; a separate change)

## Decisions

### D1: MaterializedViewInfo shape — 3-field NamedTuple

`MaterializedViewInfo(schema, name, definition)` mirrors `ViewInfo` and satisfies the `item[:-1]` identity convention.
Indexes are deliberately not a field. A field before `definition` would join the identity key. Desired state comes from
canonicalization, which creates the object without indexes, so identity keys would never match and every run would emit
a replace.

**Alternative considered:** `MaterializedViewInfo(schema, name, definition, indexes)` with custom diff handling.
Rejected: it breaks the shared `_diff_items` convention for one type.

### D2: Reconstructed DDL with a `CREATE MATERIALIZED VIEW` preamble

Same SQL-side reconstruction as views, with two differences: `relkind = 'm'`, and the preamble omits `OR REPLACE`
because PostgreSQL rejects it. The proposal's prototype confirmed identical read-back text for equivalent inputs, so
exact string comparison carries over.

The inspection query adds the `pg_depend` `deptype = 'e'` exclusion used by functions, triggers, and check constraints.
`classid` is `'pg_catalog.pg_class'::regclass` because materialized views live in `pg_class`.

### D3: Canonicalization uses drop-first plus forced `WITH NO DATA`

`ensure_or_replace()` cannot help here (prototype fact 8), and `IF NOT EXISTS` reads back the wrong definition (fact 3).
Inside the savepoint, canonicalization therefore:

1. Parses each `matview_ddl` statement and extracts `(schema, name)` from the `CreateTableAsStmt` node
1. Executes `DROP MATERIALIZED VIEW IF EXISTS <schema>.<name> CASCADE` for each declared object
1. Executes functions, then views, then the materialized view statements re-emitted via `postgast.deparse()` with
   `skip_data = True`, then triggers

`CASCADE` keeps canonicalization from failing when an undeclared view depends on a declared materialized view. The
cascade damage is confined to the savepoint. Read-back correctness is unaffected: desired state is filtered to declared
objects, and current state is inspected before `canonicalize()` runs.

Forcing `WITH NO DATA` makes autogenerate cost independent of the view query (fact 5) and changes nothing in the
read-back (fact 6).

**Alternative considered:** executing the user's statement verbatim. Rejected: a `WITH DATA` default would run arbitrary
aggregate queries on every `alembic revision --autogenerate`.

**Alternative considered:** `DROP` without `CASCADE`. Rejected: any dependent object turns autogenerate into an error
the user cannot fix from DDL declarations.

### D4: Replacement carries index DDL on the operation

The comparator attaches `inspect_matview_indexes()` output for the current object to each `ReplaceMaterializedViewOp`.
The renderer emits `DROP`, `CREATE`, then the index statements. Rationale: silent index loss breaks
`REFRESH ... CONCURRENTLY` in production (fact 7), and renderers must not query the database, so the data travels on the
operation.

`reverse()` keeps the same `index_ddl`. The indexes belong to the current object, which the downgrade re-creates, so the
statements apply cleanly in that direction. On upgrade, an index referencing a removed column fails at migration time.
That failure is visible in the generated file and editable, which beats silent loss.

**Alternative considered:** treating index loss as a documented limitation. Rejected: the failure mode is silent and
surfaces much later, at the first concurrent refresh.

### D5: Rendered `DROP` and `REPLACE` build DDL from identifiers

`postgast.to_drop()` raises for `CreateTableAsStmt` (fact 8). Both renderers build
`DROP MATERIALIZED VIEW <schema>.<name>` from catalog identifiers through `autogen_context.dialect.identifier_preparer`,
exactly like `DropViewOp`. No `CASCADE`: in a migration, failing loudly is safer than dropping undeclared dependents.

### D6: Rendered `CREATE` populates at migration time

The canonical definition has no data clause (fact 6 in Context), so the rendered `CREATE MATERIALIZED VIEW` populates by
PostgreSQL default. This is the useful behavior for a migration: the object is ready after `alembic upgrade`, and no
separate `REFRESH` step is needed. Users with expensive queries can hand-edit the generated file.

### D7: Ordering — canonicalization and emitted operations

Canonicalization execution order: matview drops, functions, views, materialized views, triggers. Emitted operation order
grows from 6 groups to 8, with materialized view drops before view drops and materialized view creates after view
creates. Triggers cannot reference materialized views (fact 9), so triggers stay last unchanged.

A regular view over a materialized view cannot be expressed under this fixed order. This mirrors the existing stance on
view-to-view dependencies and is documented as a limitation.

### D8: Configuration key `pg_materialized_views`

A separate key, consistent with one key per object type, joining `_DESIRED_STATE_KEYS` for typo detection.

**Alternative considered:** accepting materialized view DDL inside `pg_views`. Rejected: the two types have different
operations and lifecycles, and a mixed list would need per-statement dispatch and would blur `IGNORED` semantics.

## Risks / Trade-offs

**[Replacement is destructive]** → `DROP` + `CREATE` loses grants, comments, and the population state, and briefly locks
out readers. Mitigation: document; grants and comments are already outside the library's scope for other types.

**[Preserved index can contradict the new query]** → The migration fails at `CREATE INDEX`. Mitigation: the statement is
visible in the generated file; the error names the index; documented in D4.

**\[`CASCADE` in canonicalization can hide a real dependency\]** → A user who replaces a materialized view that other
objects depend on sees the failure only at migration time, not at autogenerate time. Mitigation: acceptable; the
rendered `DROP` without `CASCADE` fails loudly and early in review or CI.

**\[`CanonicalState` and `DiffResult` grow a field\]** → **BREAKING** for positional destructuring. Mitigation: fields
are appended; keyword access is unaffected; same accepted risk as the `add-views` change.

**[Downgrade of a drop loses indexes]** → `DropMaterializedViewOp.reverse()` re-creates the object without indexes.
Mitigation: documented limitation; the create case has no index source today.

## Open Questions

_(none. The prototype resolved every question that would change specs or approach.)_
