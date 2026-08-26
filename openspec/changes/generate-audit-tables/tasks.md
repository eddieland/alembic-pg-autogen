## 1. Specification Object

- [ ] 1.1 Add `src/alembic_pg_autogen/audit.py` with an `AuditSpec` `NamedTuple` — `tables`, `suffix` (`"_aud"`),
  `schema`, `events`, `exclude_columns`, `audit_columns`, and the function/trigger naming callables
- [ ] 1.2 Add default audit columns — `aud_id` identity primary key, `aud_action`, `aud_at` defaulting to `now()`,
  `aud_actor` defaulting to `current_user` — as a module-level `Final`
- [ ] 1.3 Validate the specification: every named table present in the metadata, no bookkeeping name colliding with a
  mirrored column, no non-`aud_action` bookkeeping column that is `NOT NULL` without a server default
- [ ] 1.4 Add specification tests to `tests/alembic_pg_autogen/test_audit.py` — selection, exclusion, each `ValueError`
  path

## 2. Audit Table Derivation

- [ ] 2.1 Add `_derive_audit_table(source, spec)` building `Column(name, source.type, nullable=True)` per mirrored
  column, sharing type objects by reference so a named `Enum` is not duplicated
- [ ] 2.2 Prepend the bookkeeping columns and add the two indexes — non-unique on the source primary key columns, and on
  `aud_at`
- [ ] 2.3 Confirm no constraint survives mirroring: no primary key, foreign key, unique, check, `NOT NULL`, server
  default, or identity
- [ ] 2.4 Add derivation tests — column names and types, constraint stripping, index placement, composite primary key,
  schema qualification, `Enum` sharing

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
  than an `InvalidRequestError`
- [ ] 4.3 Export `AuditSpec`, `AuditObjects`, and `add_audit_tables` from `src/alembic_pg_autogen/__init__.py` and add
  them to `__all__`
- [ ] 4.4 Update export tests in `tests/alembic_pg_autogen/test_import.py`

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
- [ ] 5.6 Verify removal — dropping a table from `AuditSpec.tables` drops its trigger, function, and audit table
- [ ] 5.7 Verify composition — generated DDL spliced alongside hand-written `pg_functions` leaves neither set dropped

## 6. Documentation

- [ ] 6.1 Add an audit generation section to `README.md` and `docs/quickstart.rst`, leading with the splice
  (`pg_functions=[*PG_FUNCTIONS, *audit.functions]`) and why assigning instead drops the user's own functions
- [ ] 6.2 Document the two hazards from the design: a dropped source column destroys that column's history (with the
  `include_object` recipe), and `add_audit_tables()` must run after every model module is imported
- [ ] 6.3 Document the `SECURITY DEFINER` note — the audit table should not be writable by the roles writing the source
  table
- [ ] 6.4 Add `AuditSpec`, `AuditObjects`, and `add_audit_tables` to `docs/api.rst`
- [ ] 6.5 Run `make lint` and `uv run mdformat --check README.md CLAUDE.md openspec/`
- [ ] 6.6 Run the full suite against PostgreSQL and confirm existing tests still pass
