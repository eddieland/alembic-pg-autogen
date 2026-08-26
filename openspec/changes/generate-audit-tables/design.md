## Context

The library manages functions, triggers, and views through one pipeline: inspect the catalog, canonicalize the desired
DDL by round-tripping it through PostgreSQL, diff the two snapshots, render `op.execute()`. Desired state arrives as DDL
strings the user wrote.

An audit table pattern is three coupled objects — a shadow table, a trigger function, a trigger — where only the first
is a table Alembic understands and the other two are DDL strings this library understands. The coupling is total: a
column added to `users` must appear in `users_aud` and in the function's `INSERT` column list, or the trigger errors at
runtime. There is no judgement in any of it, which is why it should not be typed by hand, and why the type of drift the
user hits is drift in the *downgrade* body — the one copy nothing exercises until the day it matters.

The insight this change rests on is that both halves already have an owner. Alembic owns tables and columns; this
library owns functions and triggers. Nothing new needs to own anything. What is missing is the derivation that feeds
both.

## Goals / Non-Goals

**Goals:**

- Derive the audit table, trigger function, and trigger from a `Table` in `target_metadata`
- Route each derived object to the system that already manages that object type
- Make a column change to the source table produce, with no human intervention, an `add_column` on the audit table and a
  function replacement in the same migration
- Make `downgrade()` correct by construction — never a hand-copied body
- Add no comparator, op type, or renderer

**Non-Goals:**

- Before/after image pairs, changed-column-only rows, `TRUNCATE` auditing
- Retaining audit columns whose source column was dropped (see D6)
- Auditing tables outside `target_metadata`
- Partitioned tables, retention policies, history-query helpers

## Decisions

### D1: Split the derivation across the two existing owners

`add_audit_tables()` produces three things and hands each to whoever already manages it:

| Derived object    | Handed to               | Ops it produces                                         |
| ----------------- | ----------------------- | ------------------------------------------------------- |
| `users_aud` table | the caller's `MetaData` | Alembic's `create_table` / `add_column` / …             |
| trigger function  | `pg_functions`          | this library's `CreateFunctionOp` / `ReplaceFunctionOp` |
| trigger           | `pg_triggers`           | this library's `CreateTriggerOp` / `ReplaceTriggerOp`   |

Neither system learns anything about auditing. The audit table is an ordinary `Table` and the function is an ordinary
DDL string; both are indistinguishable, downstream, from ones a user wrote.

This is what makes the downgrade correct. `ReplaceFunctionOp` carries the definition read back from `pg_proc`, so
`downgrade()` restores the body the database actually had. That op already exists and already behaves this way — the
staleness the user is fixing is not a gap in the pipeline, it is a consequence of a human being the one who writes the
`CREATE OR REPLACE`.

**Alternative considered:** a `pg_audit_tables` option read by the comparator, with a new `AuditTableOp` rendering the
whole pattern. Rejected — it would need to re-implement column diffing that Alembic already does, and it would put the
audit table outside `include_object`, `include_name`, and every other Alembic filter users already rely on.

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

Splicing rather than assigning matters and is worth saying twice in the docs: `pg_functions=audit.functions` silently
declares that the user's own functions should not exist, and this library drops undeclared objects within a managed
type. The same splice is what makes the reverse safe — dropping a table from `AuditSpec.tables` removes its function
from the declared set, so it is dropped rather than orphaned.

### D3: Mirrored columns carry name and type, and nothing else

A mirrored column is `Column(name, source.type, nullable=True)`. Every constraint is dropped:

- **Foreign keys** — a `DELETE` audit row records a parent that no longer exists; the FK would reject the write
- **Primary key, unique** — the whole point is many rows per source row
- **`NOT NULL`** — an added column's historical rows have no value, and `NULL` is the honest answer
- **Server defaults, identity, `SERIAL`** — the trigger writes the observed value; a default would invent one
- **Check constraints** — a constraint tightened today would reject rows recorded under yesterday's rule

Types are shared by reference, so a `sa.Enum` mirrors as the same PostgreSQL enum type rather than a second one.

The audit table gets its own `aud_id` identity primary key, an index on `aud_at`, and — when the source table has
primary key columns — a non-unique index on those columns, so "history of row X" is a lookup rather than a scan. A
keyless source table is audited without that second index; building one over an empty column list is not valid DDL, and
there is nothing to look a row up by.

### D4: Only `aud_action` is written by the trigger; every other audit column comes from its server default

Audit bookkeeping columns are configurable, which raises the question of how a generated function knows to populate a
column the user added. It does not, and does not need to — the rule is that bookkeeping columns are populated by their
own server defaults:

| Column       | Type          | Populated by              |
| ------------ | ------------- | ------------------------- |
| `aud_id`     | `bigint`      | identity                  |
| `aud_action` | `text`        | the trigger, from `TG_OP` |
| `aud_at`     | `timestamptz` | `DEFAULT now()`           |
| `aud_actor`  | `text`        | `DEFAULT session_user`    |

So a user adding `aud_txid bigint DEFAULT pg_current_xact_id()` gets it populated without the generator knowing what a
transaction id is, and the function body depends only on the mirrored columns. `AuditSpec` validation enforces the rule:
a bookkeeping column that is neither `aud_action` nor defaulted nor nullable is rejected at build time, not at the first
`INSERT`.

`session_user` rather than `current_user`, and the distinction is the whole value of the column. The default is
evaluated during the `INSERT` the trigger performs, which runs inside the `SECURITY DEFINER` function — where
`current_user` is the function's *owner*, identically for every writer. An audit trail whose actor column records the
same role for all rows records nothing. `session_user` is the authenticated login role and is unchanged by the privilege
switch, so it survives to the audit row.

It is also unchanged by `SET ROLE`, which is the right trade for a trail (the login is the accountable identity) but
wrong for an application that multiplexes end users over one connection. Those callers set an application actor and
declare the column themselves:

```python
Column("aud_actor", Text, server_default=text("coalesce(current_setting('app.actor', true), session_user)"))
```

which D4's rule accommodates without the generator learning anything about it.

The four defaults are a starting set, not a floor: `audit_columns` **replaces** them rather than extending them, and
only `aud_action` is structural. A service that already denormalizes request metadata into an audit-event row wants
per-row bookkeeping to be a single foreign key rather than a repeated actor and timestamp, and declares exactly that:

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
function stays unaware of it: it writes `aud_action` and the mirrored columns, and PostgreSQL fills the rest. Note that
D3's constraint stripping governs *mirrored* columns only — a bookkeeping column keeps whatever the caller declares, and
a foreign key is legitimate here because an audit-event row, unlike a deleted parent, does exist.

`audit_columns` is a **factory** rather than a `Sequence[Column]` because a SQLAlchemy `Column` cannot be attached to
two `Table` objects — reusing one instance across audited tables raises
`ArgumentError: Column object ... already assigned to Table ...`. The factory is called once per audited table, which
also keeps the audit-event foreign key pointing at one shared table without aliasing a single `ForeignKey` object across
many.

### D5: `AFTER ... FOR EACH ROW`, one function per audited table

`AFTER` rather than `BEFORE`: by then other `BEFORE` triggers have made their edits, so the recorded row is the row that
lands. One function per table rather than one shared function, because a shared one can only work by not naming columns
— `to_jsonb(NEW)` into a single `jsonb` column — and a column-mirrored audit table is what was asked for. The generated
body:

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

`SECURITY DEFINER` with a pinned `search_path` matches the pattern the e2e suite already exercises, and keeps a caller
who cannot write the audit table from being able to skip the audit. Identifiers are quoted through the dialect's
preparer, so a column named `order` or `Name` survives.

The trigger names no columns, so in practice only the function is ever replaced; the trigger changes only if `events` or
timing changes. `ReplaceTriggerOp` covers that case and needs nothing new.

### D6: Mirroring is exact, including drops

A column dropped from `users` is dropped from `users_aud`, destroying that column's history. This is the one place the
derivation does something irreversible, and the design accepts it rather than hiding it: the op is an ordinary Alembic
`drop_column` in a migration a human reviews before running.

Retention needs the derived table to remember a column no longer in the model, which means either checked-in state — the
thing this change exists to avoid — or reading the live catalog, which would make the derivation depend on a connection
and break offline autogenerate. Neither belongs in the first version. Users who need history preserved suppress the drop
with a filter Alembic already has:

```python
def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "column" and reflected and compare_to is None and obj.table.name.endswith("_aud"):
        return False  # keep audit columns whose source column is gone
    return True
```

### D7: Name collisions are a build-time error

A source table with a column named `aud_at` would collide with a bookkeeping column. `add_audit_tables()` raises
`ValueError` naming the table and the column, rather than generating a table whose two columns differ only in what wrote
them. Prefix is configurable for exactly this reason.

## Risks / Trade-offs

**R1 — op ordering between Alembic's comparators and ours.** In one migration, `add_column` on `users_aud` should
precede the function replacement, and Alembic's reversal then gives the correct downgrade for free (restore old body,
then drop the column). Our comparator appends to `upgrade_ops.ops` after Alembic's schema comparator has run, so the
order should hold from the plugin list order — but that is an assumption about registration order, and a task verifies
it end to end rather than trusting it. The blast radius if it is wrong is small: `CREATE OR REPLACE FUNCTION` does not
resolve column references at creation time for PL/pgSQL, and both ops land in one transaction, so a wrong order is
cosmetic rather than a failed migration.

**R2 — a wide audited table doubles write cost.** A row-level `AFTER` trigger on a hot table is a real cost, paid on
every write. This is inherent to the pattern rather than to generating it, but generation makes it easy to switch on for
20 tables at once. Documented, not solved.

**R3 — `add_audit_tables()` mutates its argument.** Calling it twice on the same `MetaData` must be idempotent rather
than raising `InvalidRequestError` for a duplicate table, so the implementation checks `metadata.tables` first. Finding
a name taken is not enough to proceed, though: a user migrating off a hand-rolled audit setup already has a `users_aud`
in their models, and silently adopting it would point the generated function at a table whose columns it does not match.
So the derived table is stamped `info={"alembic_pg_autogen_audit": True}`, and the check is for that stamp, not for the
name — a stamped table is ours to rebuild, an unstamped one raises `ValueError` naming the collision. Ownership is the
question being asked, and only a marker answers it; comparing structure would reject a table we built under a spec that
has since changed, and accept one someone else happened to shape the same way.

Order matters too: `add_audit_tables()` must run after every model module is imported, or it derives from a partially
populated `MetaData` and silently audits a subset.

**R4 — `SECURITY DEFINER` makes the function owner's rights the ones that matter.** A pinned `search_path` closes the
usual hole, and the audit table should not be writable by the roles writing the source table, or the trail can be
forged. Called out in the docs; not enforced by the generator, which does not manage grants.

**R5 — a bookkeeping default that reads session state fails on writes that never set it.** The trigger fires on *every*
write to an audited table, not only the ones arriving through the application: data migrations, background jobs, and a
maintenance `psql` session all count. A `NOT NULL` column defaulted to something like `current_audit_event()` therefore
turns any write from those paths into an error, and a data migration touching an audited table is the case that bites
first — it is exactly the kind of write nobody thinks of as a request.

That is a property of the caller's default expression rather than of the generator, and the generator cannot repair it
without knowing what the column means. What it can do is make the shape obvious in the docs: read the setting with
`missing_ok` and decide the fallback deliberately —
`coalesce(current_setting('app.audit_event_id', true)::bigint, create_orphan_audit_event())` to keep an out-of-band
write auditable, or letting it fail loudly if an unattributed write should be impossible. Either is defensible; the
accident is arriving at one without choosing.

Note also that a change to such a default is not detected by autogenerate today: Alembic's `compare_server_default` is
opt-in and off. The `compare-server-defaults` change proposed alongside this one is what closes that, and the two
compose — expression-level default comparison is what keeps a generated audit table's bookkeeping defaults from drifting
the same way trigger bodies do.

## Migration Plan

Additive and inert. The module does nothing until `add_audit_tables()` is called, existing configurations are
unaffected, and a user adopting it on an existing hand-written audit setup can migrate one table at a time by deleting
their DDL strings and adding the table name to `AuditSpec.tables` — the generated function either matches what the
catalog holds, in which case autogenerate emits nothing, or differs, in which case the diff shows exactly how.

## Open Questions

- Should `AuditSpec.tables=None` mean "every table in the metadata"? Convenient, but it audits new tables silently as
  they are added, including join tables and lookup tables nobody wants audited. The tasks assume an explicit list is
  required and an opt-in `Table.info` marker (`info={"audit": True}`) is the follow-up for per-model declaration.
- Whether to offer a `jsonb` body style as a second strategy. It never goes stale and needs no audit-table columns,
  which makes it strictly simpler — but it is a different schema, not a variant of this one, and belongs in its own
  change.
