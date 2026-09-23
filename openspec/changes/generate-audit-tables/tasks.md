## 1. Specification Object

- [ ] 1.1 Add `src/alembic_pg_autogen/audit.py` with an `AuditSpec` `NamedTuple`: `tables`, `suffix` (`"_aud"`),
  `schema`, `events`, `exclude_columns`, `audit_columns` (a **factory** returning fresh `Column` objects, because a
  SQLAlchemy `Column` cannot be attached to two `Table` objects), and the function/trigger naming callables
- [ ] 1.2 Add the default `audit_columns` factory: `aud_id` identity primary key, `aud_action`, `aud_at` defaulting to
  `now()`, `aud_actor` defaulting to **`session_user`** (not `current_user`, which inside the `SECURITY DEFINER`
  function is the owner). Build fresh `Column` objects on each call, and document that supplying `audit_columns`
  replaces this set rather than extending it.
- [ ] 1.3 Validate the specification (D7): every named table present in the metadata (matched against `MetaData.tables`
  keys), `events` a non-empty subset of insert / update / delete, every `exclude_columns` entry present on at least one
  audited table, no bookkeeping name colliding with a mirrored column, a replacement `audit_columns` set containing
  `aud_action`, no non-`aud_action` bookkeeping column that is `NOT NULL` without a server default, and no derived-name
  collision under a shared `AuditSpec.schema`
- [ ] 1.4 Add specification tests to `tests/alembic_pg_autogen/test_audit.py`: selection, exclusion, and each
  `ValueError` path from 1.3

## 2. Audit Table Derivation

- [ ] 2.1 Add `_derive_audit_table(source, spec)` building `Column(name, source.type, nullable=True)` per mirrored
  column, sharing type objects by reference so a named `Enum` is not duplicated
- [ ] 2.2 Prepend the bookkeeping columns by invoking `audit_columns` once per audited table, never reusing instances,
  and keep any foreign key or `NOT NULL` they declare (constraint stripping applies to mirrored columns only)
- [ ] 2.3 Stamp the table `info={"alembic_pg_autogen_audit": True}` and add the indexes: always on `aud_at`, and on the
  source primary key columns only when there are any (an index over an empty column list is not valid DDL)
- [ ] 2.4 Confirm no constraint survives mirroring: no primary key, foreign key, unique, check, `NOT NULL`, server
  default, identity, or generated column
- [ ] 2.5 Add derivation tests: column names and types, constraint stripping, index placement, keyless source table,
  composite primary key, schema qualification, `Enum` sharing
- [ ] 2.6 Add a replaced-bookkeeping test: a spec supplying `aud_id` / `aud_action` / an `audit_event_id` foreign key
  defaulted to a session-reading function, over two or more audited tables, asserting the default set is replaced, the
  foreign key survives, and no `Column` instance is shared between the derived tables

## 3. Shared Body Template and Desired DDL

- [ ] 3.1 Add one shared rendering function for the D5 body that takes the table identifiers, the mirrored column names,
  the action column name, and the events, and quotes every identifier through a module-level PostgreSQL
  `identifier_preparer` (the generator has no connection)
- [ ] 3.2 Render the desired function DDL from **metadata** columns through the shared template, and the desired trigger
  DDL (`AFTER <events> ... FOR EACH ROW EXECUTE FUNCTION`), for the detection channels
- [ ] 3.3 Return the desired DDL as instances of the marker `str` subclass from `alembic_pg_autogen.ops` (D11), carrying
  the D9 scalars: source and audit table names, schema, function and trigger names, events, and the action column name
- [ ] 3.4 Confirm determinism: two calls with the same inputs return byte-identical strings, with mirrored columns in
  metadata order
- [ ] 3.5 Add rendering tests: column enumeration, `OLD` branch for `DELETE`, bookkeeping columns absent from the
  `INSERT` list, quoting of a `order`/`Name` column, event subsets, determinism, and template equality between a
  metadata-derived and a reflection-derived render of the same table shape

## 4. Synchronization Operations

- [ ] 4.1 Add `SyncAuditOp` and `DropAuditOp` `MigrateOperation` classes and the marker `str` subclass to
  `src/alembic_pg_autogen/ops.py`, each op carrying the D9 scalars and a `missing_ok` flag
- [ ] 4.2 Assign reverses by diff action (D10): a creating sync reverses to `DropAuditOp`, a replacing sync reverses to
  `SyncAuditOp`, and `DropAuditOp` reverses to a creating `SyncAuditOp`
- [ ] 4.3 Register `sync_audit` and `drop_audit` with Alembic's `Operations` in `audit.py`, with invoke implementations
  that reflect the source and audit tables from the operation's connection, derive the mirrored columns as the live
  intersection in source order, render through the shared template, and execute the function and (when absent or
  different) trigger DDL; `drop_audit` drops trigger then function
- [ ] 4.4 Implement `missing_ok`: a lenient operation skips when the audit table is absent, a strict one raises an error
  naming the table; both raise a clear offline-mode error when no connection is available (R6)
- [ ] 4.5 Add renderers in `audit.py` emitting `op.sync_audit(...)` / `op.drop_audit(...)` calls with scalars only
- [ ] 4.6 In `compare.py`, when a diff entry's declared DDL carries the marker, emit the D10 bracket (one lenient
  operation prepended before Alembic's operations, one strict operation appended after them, one pair per audited table
  with function and trigger changes folded together) instead of literal-DDL operations; all unmarked entries render
  exactly as before
- [ ] 4.7 Add operation tests: reverse mapping per diff action, scalar-only rendering, lenient and strict behavior
  against a missing audit table, and the offline-mode error

## 5. Public Entry Point

- [ ] 5.1 Add `AuditObjects` `NamedTuple` (`tables`, `functions`, `triggers`) and `add_audit_tables(metadata, spec)`
  attaching each derived table to *metadata* and returning the marked desired DDL
- [ ] 5.2 Make attachment idempotent: check `metadata.tables` before constructing, keying on the `info` stamp rather
  than the name, so a second call is a no-op rather than an `InvalidRequestError`, and raise `ValueError` when the
  derived name is occupied by a table this generator did not produce
- [ ] 5.3 Add entry-point tests: repeat calls, and the `ValueError` for an unstamped table occupying the derived name
- [ ] 5.4 Give `audit.py` its own `__all__` and **do not** re-export from `src/alembic_pg_autogen/__init__.py` (D8). The
  module is reached as `from alembic_pg_autogen.audit import ...`, which is where the opt-in is expressed.
- [ ] 5.5 Add an isolation test to `tests/alembic_pg_autogen/test_import.py`: assert `alembic_pg_autogen.audit` imports
  no `alembic_pg_autogen.*` sibling other than `ops`, that no pipeline module imports `audit`, and that the top-level
  `__all__` is unchanged by this feature

## 6. End-to-End Verification

- [ ] 6.1 Add `tests/alembic_pg_autogen/test_e2e_audit_generation.py` covering first-run creation: one migration with
  `create_table`, a `sync_audit` bracket, and **no function or trigger DDL text in the migration file**
- [ ] 6.2 Verify the no-op case: an unchanged model against a matching database produces an empty `upgrade()`, including
  immediately after a `sync_audit` has run (D9 convergence)
- [ ] 6.3 Verify the add-column case end to end: `add_column` on both tables plus a sync bracket, asserting the
  bracket's position around the column operations **in both `upgrade()` and the reversed `downgrade()`** (R1)
- [ ] 6.4 Verify the add-column round trip: upgrade, write, downgrade, write again, and assert the post-downgrade
  function equals a fresh derivation for the baseline model (derivation identity, R7)
- [ ] 6.5 Verify the drop-column round trip: upgrade, write, downgrade, and assert a write records the restored column's
  value
- [ ] 6.6 Verify merged branches converge: generate two migrations for two different added columns, each against a
  database lacking the other's column, apply both, assert the final function writes both columns and a subsequent
  autogenerate run is empty
- [ ] 6.7 Verify the trail is written: insert, update, delete against a live database, then assert three audit rows with
  the right `aud_action`, values, `aud_at`, and `aud_actor`
- [ ] 6.8 Verify the actor survives the privilege switch: write to an audited table as a role that does not own the
  trigger function, and assert `aud_actor` is that role rather than the owner (the `current_user`/`session_user`
  distinction is invisible when the test runs as the owner, so the test must use a second role)
- [ ] 6.9 Verify removal: dropping a table from `AuditSpec.tables` drops its trigger, function, and audit table, and the
  reversed `downgrade()` recreates all three in a working state
- [ ] 6.10 Verify composition: generated DDL spliced alongside hand-written `pg_functions` leaves neither set dropped,
  and the hand-written set still renders as literal DDL
- [ ] 6.11 Verify the retained-history recipe: suppress an audit column drop via `include_object`, run `sync_audit`, and
  assert writes succeed with NULL recorded for the retained column
- [ ] 6.12 Verify offline mode: `alembic upgrade --sql` over a migration containing `sync_audit` fails with the R6 error

## 7. Documentation

- [ ] 7.1 Add an audit generation section to `README.md` and `docs/quickstart.rst`, leading with the splice
  (`pg_functions=[*PG_FUNCTIONS, *audit.functions]`) and why assigning instead drops the user's own functions
- [ ] 7.2 Document that migrations contain no function bodies: `sync_audit` derives at apply time, offline `--sql` is
  unsupported for audit operations (R6), and reconstructing a historical body means replaying to that revision (R7)
- [ ] 7.3 Document the operation registration requirement (R8): `env.py` must import `alembic_pg_autogen.audit` for as
  long as historical migrations reference `op.sync_audit` or `op.drop_audit`
- [ ] 7.4 Document the destructive-change hazards from D6 with the `include_object` recipe: dropped columns, renames
  seen as drop-plus-add, type changes that must cast history, and removal from the spec dropping the recorded trail.
  Also document that `add_audit_tables()` must run after every model module is imported.
- [ ] 7.5 Document the `SECURITY DEFINER` notes (R4): the audit table should not be writable by the roles writing the
  source table, a dedicated audit schema via `AuditSpec.schema` is the recommended shape, the pattern fails closed (D5),
  and `aud_actor` records `session_user` (with the `current_setting('app.actor', ...)` recipe for applications that
  multiplex end users over one connection)
- [ ] 7.6 Document R5: a bookkeeping default reading session state fails on writes that never set it (data migrations,
  background jobs, `psql`), with the `missing_ok` + deliberate-fallback shape, and note that such defaults are not
  compared by autogenerate until `compare-server-defaults` lands
- [ ] 7.7 Add `AuditSpec`, `AuditObjects`, `add_audit_tables`, and the two operations to `docs/api.rst`
- [ ] 7.8 Run `make lint` and `uv run mdformat --check README.md CLAUDE.md openspec/`
- [ ] 7.9 Run the full suite against PostgreSQL and confirm existing tests still pass
