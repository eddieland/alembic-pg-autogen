## Context

The library manages functions, triggers, and views through one pipeline: inspect the catalog, canonicalize the desired
DDL by round-tripping it through PostgreSQL, diff the two snapshots, render migration operations. Desired state arrives
as DDL strings the user wrote.

An audit table pattern is three coupled objects: a shadow table, a trigger function, and a trigger. Only the first is a
table Alembic understands. The other two are DDL this library understands. The coupling is total. A column added to
`users` must appear in `users_aud` and in the function's `INSERT` column list, or the trigger errors at runtime. There
is no judgement in any of it, which is why a human should not type it.

A stored function body is a copy of derivable state, and every copy can go stale. The known failure modes are all stale
copies:

- A hand-copied `downgrade()` body drifts from the audit table. The drift surfaces on the day of a rollback.
- Two branches each generate a migration with a full body. After a merge, the body that runs last was derived without
  the other branch's column. Alembic's `add_column` operations compose across a merge. Frozen bodies do not, so the
  losing column is audited as NULL until the next autogenerate run.
- A body generated against a drifted development database embeds that drift into the migration, and into its downgrade.

The design therefore stores no body anywhere. Alembic owns tables and columns. This library owns functions and triggers.
The migration records only that a synchronization must happen at that point, and the body is derived at apply time from
the live catalog (D9).

## Goals / Non-Goals

**Goals:**

- Derive the audit table, trigger function, and trigger from a `Table` in `target_metadata`
- Route each derived object to the system that already manages that object type
- Keep function bodies out of migration files, in both `upgrade()` and `downgrade()`
- Make a column change produce, with no human intervention, an `add_column` on the audit table and a function
  synchronization in the same migration
- Make the function correct after any upgrade or downgrade, including across merged branches
- Keep detection inside the existing inspect/canonicalize/diff pipeline

**Non-Goals:**

- Before and after image pairs, changed-column-only rows, `TRUNCATE` auditing
- Retaining audit columns whose source column was dropped (see D6)
- Auditing tables outside `target_metadata`
- Partitioned tables, retention policies, history-query helpers
- Offline `--sql` support for the audit operations (see R6)

## Decisions

### D1: Split the derivation across the two existing owners

`add_audit_tables()` produces three things and hands each to whoever already manages it:

| Derived object    | Handed to               | What it drives                                       |
| ----------------- | ----------------------- | ---------------------------------------------------- |
| `users_aud` table | the caller's `MetaData` | Alembic's `create_table` / `add_column` / ...        |
| function DDL      | `pg_functions`          | detection only; rendered as `op.sync_audit()` (D11)  |
| trigger DDL       | `pg_triggers`           | detection only; folded into the same sync call (D11) |

Neither system learns anything about auditing at detection time. The audit table is an ordinary `Table`. The derived DDL
is an ordinary string as far as canonicalization and diff are concerned. The special handling happens at render time and
apply time, in operations this change adds (D9, D11).

**Alternative considered:** a `pg_audit_tables` option read by the comparator, with a new `AuditTableOp` rendering the
whole pattern. Rejected. It would re-implement column diffing that Alembic already does, and it would put the audit
table outside `include_object`, `include_name`, and every other Alembic filter users already rely on.

### D2: Attach derived tables to the caller's `MetaData`, as a documented side effect

Alembic's comparator only sees tables in `target_metadata`, so the derived `Table` must be attached there before
`context.configure()`. The function is therefore named for its mutation, and returns the DDL it generated:

```python
audit = add_audit_tables(target_metadata, AuditSpec(tables=["users", "orders"]))

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
    pg_functions=[*PG_FUNCTIONS, *audit.functions],
    pg_triggers=[*PG_TRIGGERS, *audit.triggers],
)
```

Splicing rather than assigning matters, and the docs say it twice. `pg_functions=audit.functions` silently declares that
the user's own functions should not exist, and this library drops undeclared objects within a managed type. The same
splice is what makes the reverse safe. Dropping a table from `AuditSpec.tables` removes its function from the declared
set, so it is dropped rather than orphaned.

### D3: Mirrored columns carry name and type, and nothing else

A mirrored column is `Column(name, source.type, nullable=True)`. Every constraint is dropped:

- **Foreign keys**: a `DELETE` audit row records a parent that no longer exists. The FK would reject the write.
- **Primary key, unique**: the whole point is many rows per source row.
- **`NOT NULL`**: an added column's historical rows have no value, and `NULL` is the honest answer.
- **Server defaults, identity, `SERIAL`, generated columns**: the trigger writes the observed value. A default would
  invent one.
- **Check constraints**: a constraint tightened today would reject rows recorded under yesterday's rule.

Types are shared by reference, so a `sa.Enum` mirrors as the same PostgreSQL enum type rather than a second one.

The audit table gets its own `aud_id` identity primary key, an index on `aud_at`, and, when the source table has primary
key columns, a non-unique index on those columns. "History of row X" is then a lookup rather than a scan. A keyless
source table is audited without that second index. Building an index over an empty column list is not valid DDL, and
there is nothing to look a row up by.

### D4: Only `aud_action` is written by the trigger; every other audit column comes from its server default

Audit bookkeeping columns are configurable, which raises the question of how a generated function knows to populate a
column the user added. It does not, and does not need to. The rule is that bookkeeping columns are populated by their
own server defaults:

| Column       | Type          | Populated by              |
| ------------ | ------------- | ------------------------- |
| `aud_id`     | `bigint`      | identity                  |
| `aud_action` | `text`        | the trigger, from `TG_OP` |
| `aud_at`     | `timestamptz` | `DEFAULT now()`           |
| `aud_actor`  | `text`        | `DEFAULT session_user`    |

So a user adding `aud_txid bigint DEFAULT pg_current_xact_id()` gets it populated without the generator knowing what a
transaction id is, and the function body depends only on the mirrored columns. `AuditSpec` validation enforces the rule.
A bookkeeping column that is neither `aud_action` nor defaulted nor nullable is rejected at build time, not at the first
`INSERT`.

The actor default is `session_user` rather than `current_user`, and the distinction is the whole value of the column.
The default is evaluated during the `INSERT` the trigger performs, which runs inside the `SECURITY DEFINER` function.
There, `current_user` is the function's *owner*, identically for every writer. An audit trail whose actor column records
the same role for all rows records nothing. `session_user` is the authenticated login role and is unchanged by the
privilege switch, so it survives to the audit row.

It is also unchanged by `SET ROLE`. That is the right trade for a trail (the login is the accountable identity) but
wrong for an application that multiplexes end users over one connection. Those callers set an application actor and
declare the column themselves:

```python
Column("aud_actor", Text, server_default=text("coalesce(current_setting('app.actor', true), session_user)"))
```

which D4's rule accommodates without the generator learning anything about it.

The four defaults are a starting set, not a floor. `audit_columns` **replaces** them rather than extending them, and
only `aud_action` is structural: a replacement set must still contain an `aud_action` column, and validation rejects one
that does not. The generated function writes `aud_action` unconditionally, so its absence would fail at the first write
rather than at build time. A service that already denormalizes request metadata into an audit-event row wants per-row
bookkeeping to be a single foreign key rather than a repeated actor and timestamp, and declares exactly that:

```python
audit_columns = lambda: [
    Column("aud_id", BigInteger, Identity(), primary_key=True),
    Column("aud_action", Text, nullable=False),
    Column(
        "audit_event_id",
        BigInteger,
        ForeignKey("audit_event.id"),
        nullable=False,
        server_default=text("current_audit_event()"),
    ),
]
```

`current_audit_event()` reading a session GUC set at the start of the request is the intended shape, and the generated
function stays unaware of it. It writes `aud_action` and the mirrored columns, and PostgreSQL fills the rest. Note that
D3's constraint stripping governs *mirrored* columns only. A bookkeeping column keeps whatever the caller declares, and
a foreign key is legitimate here because an audit-event row, unlike a deleted parent, does exist.

`audit_columns` is a **factory** rather than a `Sequence[Column]` because a SQLAlchemy `Column` cannot be attached to
two `Table` objects. Reusing one instance across audited tables raises
`ArgumentError: Column object ... already assigned to Table ...`. The factory is called once per audited table, which
also keeps the audit-event foreign key pointing at one shared table without aliasing a single `ForeignKey` object across
many.

### D5: `AFTER ... FOR EACH ROW`, one function per audited table

`AFTER` rather than `BEFORE`: by then other `BEFORE` triggers have made their edits, so the recorded row is the row that
lands. One function per table rather than one shared function, because a shared one can only work by not naming columns
(`to_jsonb(NEW)` into a single `jsonb` column), and a column-mirrored audit table is what was asked for. The body
template:

```sql
CREATE OR REPLACE FUNCTION public.users_aud_fn() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO public.users_aud (aud_action, "id", "name", "email")
        VALUES (TG_OP, OLD."id", OLD."name", OLD."email");
        RETURN OLD;
    END IF;
    INSERT INTO public.users_aud (aud_action, "id", "name", "email")
    VALUES (TG_OP, NEW."id", NEW."name", NEW."email");
    RETURN NEW;
END;
$$
```

This is a template, and it runs in two places (D9). At autogenerate time the column list comes from metadata, to build
the desired DDL the diff compares. At apply time the column list comes from the live catalog, inside `sync_audit`. Both
paths call the same rendering function, so there is one implementation of the body.

`SECURITY DEFINER` with a pinned `search_path` matches the pattern the e2e suite already exercises, and keeps a caller
who cannot write the audit table from being able to skip the audit. Identifiers are quoted through the dialect's
preparer, so a column named `order` or `Name` survives.

The pattern fails closed, and that is a decision rather than an accident. A trigger that cannot write its audit row
aborts the write to the source table. An audit trail that drops rows on error is not a trail. The docs state this,
because it is also an availability property: a broken audit object blocks writes to the audited table until repaired.

The trigger names no columns, so a column change never alters the trigger DDL. The trigger changes only if `events` or
timing changes, and `sync_audit` re-executes it in that case.

### D6: Mirroring is exact, including drops

A column dropped from `users` is dropped from `users_aud`, destroying that column's history. This is the one place the
derivation does something irreversible, and the design accepts it rather than hiding it. The op is an ordinary Alembic
`drop_column` in a migration a human reviews before running.

Three neighboring hazards get the same treatment (documented, visible in the migration, not softened by machinery):

- **Renames.** Alembic sees a rename as a drop plus an add unless the user intervenes. The audit column is dropped with
  its history and a fresh column is added.
- **Type changes.** An `alter_column` type change propagates to the audit table, where historical rows must cast. A
  narrowing change can fail on old values, and a rewrite of a large audit table holds a long lock.
- **Removal from the spec.** Dropping a table from `AuditSpec.tables` drops its trigger, its function, and the audit
  table with all recorded history.

Retention needs the derived table to remember a column no longer in the model. That means either checked-in state (the
thing this change exists to avoid) or reading the live catalog at autogenerate time, which would break offline
autogenerate. Neither belongs in the first version. Users who need history preserved suppress the drop with a filter
Alembic already has, keyed on the marker stamp's table names rather than a hardcoded suffix:

```python
AUDIT_TABLES = {t.name for t in target_metadata.tables.values() if t.info.get("alembic_pg_autogen_audit")}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "column" and reflected and compare_to is None and obj.table.name in AUDIT_TABLES:
        return False  # keep audit columns whose source column is gone
    return True
```

A retained column is absent from the source table, so the apply-time intersection (D9) stops writing it, and its later
rows are NULL. That is the honest value for history the model no longer produces.

### D7: Collisions and unknown names are build-time errors

`add_audit_tables()` raises `ValueError`, naming the offending table and column, for each of these:

- A source table named in `AuditSpec.tables` that is absent from the metadata. Names are matched against
  `MetaData.tables` keys, so a table under an explicit schema is named `"schema.table"`.
- A column named in `exclude_columns` that no audited table has. A typo here would silently audit a column the user
  meant to exclude.
- A source column whose name collides with a bookkeeping column, such as `aud_at`. The prefix is configurable for
  exactly this reason.
- Two audited tables whose derived names collide. `AuditSpec.schema` places every audit table in one schema, so auditing
  `a.users` and `b.users` with a shared audit schema derives the same name twice. With `schema=None` each audit table
  lands in its source table's schema.

### D8: `audit.py` is an extension in everything but packaging

The feature ships inside the distribution because it is cheap to carry, but it is not part of the pipeline, and the
boundary is enforced rather than described:

- **The pipeline never imports it.** No comparator, canonicalizer, or inspector imports `audit`. The shared vocabulary
  (the operation classes and the DDL marker type of D11) lives in `alembic_pg_autogen.ops`, which both sides already
  know. `audit.py` imports SQLAlchemy, Alembic's operation plumbing, and `ops`; nothing imports `audit.py`.
- **Detection does not branch on generated-ness.** Canonicalize and diff treat the derived DDL as ordinary strings. The
  single marker check (D11) happens when `compare.py` converts diff results into operations, and it consults a type, not
  the audit module.
- **It registers no autogenerate plugin.** `__init__.py` sets up `alembic_pg_autogen.compare` and
  `alembic_pg_autogen.checkconstraints` at import. Audit generation adds no third, because it contributes no comparator.
  Importing `alembic_pg_autogen.audit` registers the `sync_audit` and `drop_audit` operations with Alembic, and nothing
  else.
- **It is not re-exported from `__init__`.** `AuditSpec`, `AuditObjects`, and `add_audit_tables` are reachable only as
  `from alembic_pg_autogen.audit import ...`. The import path is where the opt-in is expressed, in the manner of
  `sqlalchemy.ext.*`.

**Trade-off:** the last point breaks the package's convention that every public name is available from the top-level
namespace. That is the intended cost. Consistency would make the extension look like a core feature, which is the thing
being avoided.

The seam that is *not* mechanical is worth naming: generated function and trigger names join `pg_functions` and
`pg_triggers`, which are whole-truth namespaces. Assigning rather than splicing (`pg_functions=audit.functions`) drops
the user's own functions. That is the one way a mistake in audit land damages non-audit objects. It stays documentation
rather than machinery, because a separate `pg_audit_functions` key would mean editing the comparator's whole-truth
semantics and forfeiting the properties above.

### D9: The migration stores no function body; `sync_audit` derives it at apply time

The rendered operation carries scalars only:

```python
op.sync_audit(
    "users",
    audit_table="users_aud",
    schema="public",
    function="users_aud_fn",
    trigger="users_aud_trg",
    events=("insert", "update", "delete"),
    action_column="aud_action",
)
```

At apply time the operation reflects the source table and the audit table from its connection, inside the migration
transaction, after the operations that precede it. It derives:

- **Mirrored columns**: the columns present in both tables, in the source table's order.
- **Bookkeeping columns**: the columns present only in the audit table. The operation writes none of them except
  `action_column`.

It then renders the D5 template over that column list and executes the `CREATE OR REPLACE FUNCTION`, followed by the
trigger DDL when the live trigger is absent or differs. `drop_audit` is the removal counterpart: it drops the trigger
and the function, and carries the same scalars so its reverse can recreate them.

Two derivation sources were rejected:

- **`target_metadata` at apply time.** Metadata is head state. Replaying migration 3 of 50 on a fresh database would
  build the final function shape 47 migrations early. Replayability requires each migration to be deterministic at its
  point in the chain, and the live catalog at that point is exactly that.
- **A column list frozen into the migration file.** Frozen lists restore the merge problem: two branches freeze two
  lists, and the one that runs last wins. The live intersection is the only source that composes the way Alembic's
  `add_column` operations compose.

The intersection rule also removes two things from the apply-time contract. `exclude_columns` needs no representation,
because an excluded column is absent from the audit table and falls out of the intersection. `audit_columns` needs no
representation, because bookkeeping columns are the audit-only columns and only `action_column` is written.

After the operation runs, the live objects equal what autogenerate derives from metadata for the same model. That
convergence is what keeps the next autogenerate run empty, and it is asserted end to end.

### D10: Sync operations bracket Alembic's operations in the same migration

Alembic builds `downgrade()` by reversing the operation list. A single trailing `sync_audit` in `upgrade()` would
therefore run *first* in `downgrade()`, re-derive a body that still references the column, and leave a broken trigger
after the column drops that follow. That is the exact defect this change exists to prevent, reintroduced by ordering.

The comparator therefore emits sync operations in a bracket around Alembic's operations: one lenient operation prepended
before them, one strict operation appended after them. Reversal preserves the bracket, so **both** directions end with a
synchronization that runs after every column change. The leading operation is lenient (`missing_ok=True`): it skips when
the audit table does not exist, which is the normal state on the creation and removal boundaries. The trailing operation
is strict, so a genuinely missing audit table fails the migration loudly.

Reverses are assigned by the diff action, not hardcoded to self-inverse. A synchronization that *creates* the objects
reverses to `drop_audit`; one that *replaces* a body reverses to `sync_audit`; `drop_audit` reverses to a creating
`sync_audit`. The four cases:

| Migration   | `upgrade()`                                                   | `downgrade()` (reversed)                                      |
| ----------- | ------------------------------------------------------------- | ------------------------------------------------------------- |
| first run   | lenient sync skips; `create_table`; sync creates fn + trg     | drop_audit removes trg + fn; `drop_table`; lenient drop no-op |
| add column  | lenient sync no-op; `add_column` x2; sync re-derives          | sync no-op; `drop_column` x2; lenient sync re-derives         |
| drop column | lenient sync no-op; `drop_column` x2; sync re-derives         | sync no-op; `add_column` x2; lenient sync re-derives          |
| removal     | drop_audit removes trg + fn; `drop_table`; lenient drop no-op | lenient sync skips; `create_table`; sync creates fn + trg     |

In every row, the last operation of each direction leaves the function and trigger consistent with the tables as that
direction leaves them. The bracket costs one near-no-op line per migration, and the e2e suite asserts the ordering in
both directions rather than trusting it.

### D11: Detection is unchanged; a marker on the derived DDL selects the rendering

The derived function and trigger DDL must stay declared in `pg_functions` and `pg_triggers`. Those are whole-truth
namespaces: a live function matching nothing declared gets a `DROP`, so removing audit DDL from the channels would make
the comparator destroy the generated objects. Detection therefore keeps the existing path end to end. Metadata derives
desired DDL, canonicalization normalizes it, and the diff decides create, replace, or drop.

The change is confined to how diff results become operations. `add_audit_tables()` returns the derived DDL as instances
of a `str` subclass defined in `alembic_pg_autogen.ops`, carrying the D9 scalars. The strings pass through
`_resolve_ddl`, canonicalization, and identity parsing untouched. When `compare.py` converts diff results into
operations, entries whose declared DDL carries the marker become `sync_audit` / `drop_audit` brackets (one pair per
audited table, with function and trigger changes folded together) instead of literal-DDL operations. Everything else
renders exactly as before.

This costs the change its "no new operation type or renderer" claim: two operation types, their renderers, their
`Operations` registrations, and one `isinstance` check in `compare.py`. The alternative (a second comparator owned by
the audit module) was rejected because it would fork whole-truth accounting and re-implement the diff.

## Risks / Trade-offs

**R1: operation ordering between Alembic's comparators and ours.** Handled by D10's bracket rather than by an assumption
about plugin registration order. The e2e suite asserts the position of the sync operations relative to the column
operations in both `upgrade()` and `downgrade()`, for the add-column and drop-column cases, and fails loudly if the
bracket does not hold.

**R2: a wide audited table doubles write cost.** A row-level `AFTER` trigger on a hot table is a real cost, paid on
every write. This is inherent to the pattern rather than to generating it, but generation makes it easy to switch on for
20 tables at once. Documented, not solved.

**R3: `add_audit_tables()` mutates its argument.** Calling it twice on the same `MetaData` must be idempotent rather
than raising `InvalidRequestError` for a duplicate table, so the implementation checks `metadata.tables` first. Finding
a name taken is not enough to proceed, though. A user migrating off a hand-rolled audit setup already has a `users_aud`
in their models, and silently adopting it would point the generated function at a table whose columns it does not match.
So the derived table is stamped `info={"alembic_pg_autogen_audit": True}`, and the check is for that stamp, not for the
name. A stamped table is ours to rebuild. An unstamped one raises `ValueError` naming the collision. Ownership is the
question being asked, and only a marker answers it. Comparing structure would reject a table we built under a spec that
has since changed, and accept one someone else happened to shape the same way.

Order matters too: `add_audit_tables()` must run after every model module is imported, or it derives from a partially
populated `MetaData` and silently audits a subset.

**R4: `SECURITY DEFINER` makes the function owner's rights the ones that matter.** A pinned `search_path` closes the
usual hole, and the audit table should not be writable by the roles writing the source table, or the trail can be
forged. Called out in the docs; not enforced by the generator, which does not manage grants. Placing audit tables in a
dedicated schema via `AuditSpec.schema`, with no grants for application roles, is the recommended shape.

**R5: a bookkeeping default that reads session state fails on writes that never set it.** The trigger fires on *every*
write to an audited table, not only the ones arriving through the application. Data migrations, background jobs, and a
maintenance `psql` session all count. A `NOT NULL` column defaulted to something like `current_audit_event()` therefore
turns any write from those paths into an error, and a data migration touching an audited table is the case that bites
first.

That is a property of the caller's default expression rather than of the generator, and the generator cannot repair it
without knowing what the column means. What it can do is make the shape obvious in the docs: read the setting with
`missing_ok` and choose the fallback deliberately.
`coalesce(current_setting('app.audit_event_id', true)::bigint, create_orphan_audit_event())` keeps an out-of-band write
auditable; omitting the fallback makes an unattributed write fail loudly. Either is defensible. The accident is arriving
at one without choosing.

Note also that a change to such a default is not detected by autogenerate today. Alembic's `compare_server_default` is
opt-in and off. The `compare-server-defaults` change proposed alongside this one closes that, and the two compose.

**R6: offline `--sql` mode cannot derive.** `sync_audit` and `drop_audit` need a connection, and offline mode has none.
The operations raise a clear error naming the limitation when invoked offline. Deriving from metadata instead would be
wrong for every migration except head (D9), and emitting a PL/pgSQL `DO` block that derives server-side would be a
second implementation of the derivation, which is the dual-source drift this change exists to remove. An explicit
column-list escape hatch for offline shops is an open question below.

**R7: the applied body tracks the installed library version.** Replaying a chain re-derives bodies with the current
generator, so a generator change (quoting, formatting, a new clause) yields bodies that differ from what an older
version originally applied. Autogenerate detects any such difference as an ordinary function diff and repairs it in the
next migration. The round-trip guarantee is therefore *derivation identity* (upgrade then downgrade leaves the function
equal to a fresh derivation for that model state), not byte identity with what history applied. The migration file also
stops being a historical record of the body. Reconstructing "the body as of revision X" means replaying to that
revision.

**R8: historical migrations depend on the operation registration.** A migration calling `op.sync_audit` fails with
`AttributeError` if nothing imported `alembic_pg_autogen.audit` in that process. `env.py` performs the import by calling
`add_audit_tables`, and must keep an import of the module even if a user later stops auditing, for as long as historical
migrations reference the operations. Documented prominently.

## Migration Plan

Additive and inert. The module does nothing until `add_audit_tables()` is called, and existing configurations are
unaffected. A user adopting it on an existing hand-written audit setup migrates one table at a time: delete the DDL
strings, add the table name to `AuditSpec.tables`, and autogenerate either emits nothing (the derivation matches the
catalog) or emits a sync bracket whose effect the diff shows.

## Open Questions

- Should `AuditSpec.tables=None` mean "every table in the metadata"? Convenient, but it audits new tables silently as
  they are added, including join tables and lookup tables nobody wants audited. The tasks assume an explicit list is
  required and an opt-in `Table.info` marker (`info={"audit": True}`) is the follow-up for per-model declaration.
- Whether to offer a `jsonb` body style as a second strategy. It never depends on columns, so it would remove function
  synchronization entirely, but it is a different schema, not a variant of this one, and belongs in its own change.
- Whether `sync_audit` should accept an explicit `columns=` argument for offline `--sql` shops. It restores offline
  support at the price of restoring frozen-list merge behavior (D9), so it must not become the default.
