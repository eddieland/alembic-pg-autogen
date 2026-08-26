## 1. Specification Object

- [ ] 1.1 Add `src/alembic_pg_autogen/audit.py` with an `AuditSpec` `NamedTuple` — `tables`, `suffix` (`"_aud"`),
  `schema`, `events`, `exclude_columns`, `audit_columns` (a **factory** returning fresh `Column` objects — a SQLAlchemy
  `Column` cannot be attached to two `Table` objects), and the function/trigger naming callables
- [ ] 1.2 Add the default `audit_columns` factory — `aud_id` identity primary key, `aud_action`, `aud_at` defaulting to
  `now()`, `aud_actor` defaulting to **`session_user`** (not `current_user`, which inside the `SECURITY DEFINER`
  function is the owner) — building fresh `Column` objects on each call, and document that supplying `audit_columns`
  replaces this set rather than extending it
- [ ] 1.3 Validate the specification: every named table present in the metadata, `events` a non-empty subset of insert /
  update / delete, no bookkeeping name colliding with a mirrored column, no non-`aud_action` bookkeeping column that is
  `NOT NULL` without a server default
- [ ] 1.4 Add specification tests to `tests/alembic_pg_autogen/test_audit.py` — selection, exclusion, each `ValueError`
  path including empty and unsupported `events`

## 2. Audit Table Derivation

- [ ] 2.1 Add `_derive_audit_table(source, spec)` building `Column(name, source.type, nullable=True)` per mirrored
  column, sharing type objects by reference so a named `Enum` is not duplicated
- [ ] 2.2 Prepend the bookkeeping columns by invoking `audit_columns` once per audited table, never reusing instances,
  and keep any foreign key or `NOT NULL` they declare — constraint stripping applies to mirrored columns only
- [ ] 2.3 Stamp the table `info={"alembic_pg_autogen_audit": True}` and add the indexes — always on `aud_at`, and on the
  source primary key columns only when there are any (an index over an empty column list is not valid DDL)
- [ ] 2.4 Confirm no constraint survives mirroring: no primary key, foreign key, unique, check, `NOT NULL`, server
  default, or identity
- [ ] 2.5 Add derivation tests — column names and types, constraint stripping, index placement, keyless source table,
  composite primary key, schema qualification, `Enum` sharing
- [ ] 2.6 Add a replaced-bookkeeping test — a spec supplying `aud_id` / `aud_action` / an `audit_event_id` foreign key
  defaulted to a session-reading function, over two or more audited tables, asserting the default set is replaced, the
  foreign key survives, and no `Column` instance is shared between the derived tables

## 3. DDL Generation

- [ ] 3.1 Add `_render_audit_function(source, audit_table, spec)` emitting the `SECURITY DEFINER` PL/pgSQL body from D5,
  quoting every identifier through `conn.dialect.identifier_preparer` (use a module-level PostgreSQL preparer — the
  generator has no connection)
- [ ] 3.2 Add `_render_audit_trigger(source, spec)` emitting `AFTER <events> ... FOR EACH ROW EXECUTE FUNCTION`
- [ ] 3.3 Confirm determinism: two calls with the same inputs return byte-identical strings, with mirrored columns in
  metadata order
- [ ] 3.4 Add rendering tests — column enumeration, `OLD` branch for `DELETE`, bookkeeping columns absent from the
  `INSERT` list, quoting of a `order`/`Name` column, event subsets, determinism

## 4. Public Entry Point

- [ ] 4.1 Add `AuditObjects` `NamedTuple` (`tables`, `functions`, `triggers`) and `add_audit_tables(metadata, spec)`
  attaching each derived table to *metadata* and returning the generated DDL
- [ ] 4.2 Make attachment idempotent — check `metadata.tables` before constructing, so a second call is a no-op rather
  than an `InvalidRequestError`, keying the check on the `info` stamp rather than the name, and raising `ValueError`
  when the derived name is occupied by a table this generator did not produce
- [ ] 4.3 Add entry-point tests — repeat calls, and the `ValueError` for an unstamped table occupying the derived name
- [ ] 4.4 Export `AuditSpec`, `AuditObjects`, and `add_audit_tables` from `src/alembic_pg_autogen/__init__.py` and add
  them to `__all__`
- [ ] 4.5 Update export tests in `tests/alembic_pg_autogen/test_import.py`

## 5. End-to-End Verification

- [ ] 5.1 Add `tests/alembic_pg_autogen/test_e2e_audit_generation.py` covering first-run creation of table, function,
  and trigger in one migration
- [ ] 5.2 Verify the no-op case — an unchanged model against a matching database produces an empty `upgrade()`
- [ ] 5.3 Verify the add-column case end to end: `add_column` on both tables plus a function replacement, and **assert
  the audit table's `add_column` precedes the function replacement in `upgrade_ops.ops`** (R1 — this is the assumption
  about plugin registration order, so fail loudly if it does not hold)
- [ ] 5.4 Verify the downgrade body is the definition read back from `pg_proc`: upgrade, downgrade, and compare the
  function definition against the pre-upgrade catalog snapshot byte for byte
- [ ] 5.5 Verify the trail is actually written — insert, update, delete against a live database, then assert three audit
  rows with the right `aud_action`, values, `aud_at`, and `aud_actor`
- [ ] 5.6 Verify the actor survives the privilege switch — write to an audited table as a role that does not own the
  trigger function, and assert `aud_actor` is that role rather than the owner (the `current_user`/`session_user`
  distinction is invisible when the test runs as the owner, so the test must use a second role)
- [ ] 5.7 Verify removal — dropping a table from `AuditSpec.tables` drops its trigger, function, and audit table
- [ ] 5.8 Verify composition — generated DDL spliced alongside hand-written `pg_functions` leaves neither set dropped

## 6. Documentation

- [ ] 6.1 Add an audit generation section to `README.md` and `docs/quickstart.rst`, leading with the splice
  (`pg_functions=[*PG_FUNCTIONS, *audit.functions]`) and why assigning instead drops the user's own functions
- [ ] 6.2 Document the two hazards from the design: a dropped source column destroys that column's history (with the
  `include_object` recipe), and `add_audit_tables()` must run after every model module is imported
- [ ] 6.3 Document the `SECURITY DEFINER` notes — the audit table should not be writable by the roles writing the source
  table, and `aud_actor` records `session_user` (with the `current_setting('app.actor', …)` recipe for applications that
  multiplex end users over one connection)
- [ ] 6.4 Document R5 — a bookkeeping default reading session state fails on writes that never set it (data migrations,
  background jobs, `psql`), with the `missing_ok` + deliberate-fallback shape, and note that such defaults are not
  compared by autogenerate until `compare-server-defaults` lands
- [ ] 6.5 Add `AuditSpec`, `AuditObjects`, and `add_audit_tables` to `docs/api.rst`
- [ ] 6.6 Run `make lint` and `uv run mdformat --check README.md CLAUDE.md openspec/`
- [ ] 6.7 Run the full suite against PostgreSQL and confirm existing tests still pass
