## Why

Row-level audit tables are the most common reason people reach for this library, and the most common thing they get
wrong. The pattern needs three objects kept in lockstep — a `users_aud` shadow table, a trigger function that enumerates
every column, and a trigger — and today all three are hand-written. Adding one column to `users` means remembering to
add it to `users_aud`, retype the function body, and hand-copy the *old* body into `downgrade()`.

That last step is where it rots. Nothing checks a downgrade clause, so the stale copy is only discovered when someone
actually rolls back, and by then the trigger writes into a column the audit table no longer has. The audit trail is the
one place silent drift is least acceptable.

Nothing about these three objects is a decision. They are all derivable from the `Table` already in `target_metadata`.

## What Changes

- Add `alembic_pg_autogen.audit` with an `AuditSpec` config and `add_audit_tables(metadata, spec)`, which derives the
  audit objects for each selected table and returns generated DDL
- Attach each derived `<table><suffix>` `Table` to the caller's `MetaData`, so **Alembic's own** table and column
  comparators own its lifecycle — `create_table` on first run, `add_column` when the source table gains a column
- Return generated `CREATE OR REPLACE FUNCTION` and `CREATE TRIGGER` strings for the existing `pg_functions` and
  `pg_triggers` channels, so the existing inspect/canonicalize/diff pipeline emits `ReplaceFunctionOp` when a column
  changes the body

No new comparator, op type, or renderer. The feature is a pure function from metadata to
`(Table, function DDL, trigger DDL)`; every op it produces comes from machinery that already exists and is already
tested.

The migration still contains the DDL — it must, to be replayable. What changes is that no human types it, and
`downgrade()` carries the definition read back from `pg_proc` rather than a hand-copied guess.

## Non-goals

- **Storing before *and* after images** — one row per event holding the affected row (NEW for insert/update, OLD for
  delete). Column-pair or changed-column-only layouts are a different schema
- **`TRUNCATE` auditing** — statement-level, no `OLD`/`NEW` to record
- **Retaining audit columns after the source column is dropped** — mirroring is exact; the `include_object` recipe in
  the design covers users who need history preserved
- **Auditing tables absent from `target_metadata`** — there is nothing to derive from
- **Partitioned tables, retention/pruning, and querying helpers** — schema generation only
- **A checked-in escape hatch** — a user who needs a hand-written body keeps using `pg_functions` directly

## Capabilities

### New Capabilities

- `audit-generation`: derivation of audit tables, trigger functions, and triggers from SQLAlchemy metadata

## Impact

- **Config**: opt-in — one `add_audit_tables()` call in `env.py`, splicing its output into `pg_functions` /
  `pg_triggers`
- **Behavior**: none for existing users; the module is inert until called
- **Dependencies**: none new
- **Public API**: new names on `alembic_pg_autogen.audit` — `AuditSpec`, `AuditObjects`, `add_audit_tables` — reached by
  importing that module, not re-exported from the top-level namespace (D8)
- **Isolation**: the module imports nothing from `alembic_pg_autogen`, nothing imports it, it registers no plugin, and
  it modifies no existing capability — an extension in everything but packaging
