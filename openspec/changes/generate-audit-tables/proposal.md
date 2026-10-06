## Why

Row-level audit tables are the most common reason people reach for this library. The pattern needs three objects kept in
lockstep: a `users_aud` shadow table, a trigger function that enumerates every column, and a trigger. Today all three
are hand-written. Adding one column to `users` means adding it to `users_aud`, retyping the function body, and
hand-copying the old body into `downgrade()`.

Every stored copy of the function body is a place the pattern can rot:

- Nothing checks a hand-copied `downgrade()` body. The stale copy is discovered on the day of a rollback, when the
  trigger writes into a column the audit table no longer has.
- Two branches each generate a migration with a full body. After a merge, the body that runs last was derived without
  the other branch's column. That column stops being audited, silently, until the next autogenerate run.
- A body generated against a drifted development database embeds that drift into the migration.

Nothing about these three objects is a decision. They are all derivable from the `Table` already in `target_metadata`.
The function body is also re-derivable at apply time from the live catalog. So the migration does not need to contain a
function body at all, in either direction.

## What Changes

- Add `alembic_pg_autogen.audit` with an `AuditSpec` config and `add_audit_tables(metadata, spec)`, which derives the
  audit objects for each selected table
- Attach each derived `<table><suffix>` `Table` to the caller's `MetaData`, so **Alembic's own** table and column
  comparators own its lifecycle (`create_table` on first run, `add_column` when the source table gains a column)
- Return derived `CREATE OR REPLACE FUNCTION` and `CREATE TRIGGER` strings for the existing `pg_functions` and
  `pg_triggers` channels. The pipeline uses these for detection only: inspect, canonicalize, and diff decide whether the
  live objects differ from the derived state.
- Render the resulting operations as `op.sync_audit(...)` and `op.drop_audit(...)` calls instead of literal DDL. At
  apply time, `sync_audit` reads the source and audit tables from the live catalog, derives the function body, and
  executes the `CREATE OR REPLACE FUNCTION` and trigger DDL.
- Add the two operation types, their renderers, and their Alembic `Operations` registrations. The detection machinery
  (comparator, canonicalization, diff) is unchanged except for one marker check at render time.

The migration therefore contains no function body. `upgrade()` and `downgrade()` both call `sync_audit`, and each call
derives the body that matches the tables as they exist at that point in the migration. A stale body has no
representation, so it cannot be written, merged, or hand-copied into existence.

## Non-goals

- **Storing before and after images**: one row per event holds the affected row (NEW for insert and update, OLD for
  delete). Column-pair and changed-column-only layouts are a different schema.
- **`TRUNCATE` auditing**: statement-level, with no `OLD` or `NEW` row to record.
- **Retaining audit columns after the source column is dropped**: mirroring is exact. The `include_object` recipe in the
  design covers users who need history preserved.
- **Auditing tables absent from `target_metadata`**: there is nothing to derive from.
- **Partitioned tables, retention and pruning, and querying helpers**: schema generation only.
- **A checked-in escape hatch**: a user who needs a hand-written body keeps using `pg_functions` directly.
- **Offline `--sql` mode for the audit operations**: `sync_audit` reads the live catalog, and offline mode has no
  connection. The operation raises a clear error in offline mode (design R6).

## Capabilities

### New Capabilities

- `audit-generation`: derivation of audit tables, trigger functions, and triggers from SQLAlchemy metadata, with
  function bodies re-derived at apply time rather than stored in migrations

## Impact

- **Config**: opt-in. One `add_audit_tables()` call in `env.py`, splicing its output into `pg_functions` and
  `pg_triggers`.
- **Behavior**: none for existing users. The module is inert until called.
- **Dependencies**: none new.
- **Public API**: new names on `alembic_pg_autogen.audit` (`AuditSpec`, `AuditObjects`, `add_audit_tables`), reached by
  importing that module, not re-exported from the top-level namespace (D8). New operation types and a shared DDL marker
  type live in `alembic_pg_autogen.ops`.
- **Migration files**: generated migrations call `op.sync_audit` and `op.drop_audit`. Importing
  `alembic_pg_autogen.audit` registers those operations. `env.py` must keep that import for as long as historical
  migrations reference them (design R8).
- **Isolation**: the pipeline never imports the audit module. The audit module imports only SQLAlchemy, Alembic's
  operation plumbing, and `alembic_pg_autogen.ops` for the shared operation types. `compare.py` gains one marker check
  at render time (D11). It registers no plugin and modifies no existing capability.
