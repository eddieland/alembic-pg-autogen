## 1. Test module setup and DDL helpers

- [ ] 1.1 Create `tests/alembic_pg_autogen/test_e2e_audit.py` with a local `_autogenerate()` helper (same pattern as
  `test_autogenerate.py`) and the `@pytest.mark.integration` marker on all test classes
- [ ] 1.2 Add Shape A DDL helper functions: `_shape_a_function(schema, table)` returns a `CREATE OR REPLACE FUNCTION`
  string for a per-table audit function with `SECURITY DEFINER`, PL/pgSQL body inserting into an audit table;
  `_shape_a_trigger(schema, table)` returns the corresponding `CREATE TRIGGER` string
- [ ] 1.3 Add Shape B DDL helper functions: `_shared_audit_function(schema)` returns a `CREATE OR REPLACE FUNCTION`
  string for a shared audit function using `TG_TABLE_NAME` and `row_to_json(NEW)` with `SECURITY DEFINER`;
  `_shape_b_trigger(schema, table)` returns a `CREATE TRIGGER` referencing the shared function
- [ ] 1.4 Add a `_setup_tables(project, tables)` helper that creates the audit log table and the N subject tables in the
  project's schema (tables: `users`, `orders`, `payments`, `products`, `audit_log`)

## 2. Shape A tests

- [ ] 2.1 Test initial creation: no audit objects in DB, desired state declares 5 functions + 5 triggers → migration has
  5 `CREATE FUNCTION` and 5 `CREATE TRIGGER` with functions before triggers in upgrade
- [ ] 2.2 Test no-op: create all 5 functions + 5 triggers in DB first, desired state matches → migration has no
  operations in `upgrade()`
- [ ] 2.3 Test function body modification: create all objects in DB, change one function body in desired state →
  migration has exactly 1 replace operation for the modified function and no trigger operations

## 3. Shape B tests

- [ ] 3.1 Test initial creation: no audit objects in DB, desired state declares 1 shared function + 5 triggers →
  migration has 1 `CREATE FUNCTION` and 5 `CREATE TRIGGER` with the function before triggers
- [ ] 3.2 Test add trigger for new table: create shared function + 5 triggers in DB, desired state adds a 6th trigger →
  migration has exactly 1 `CREATE TRIGGER` and no function operations
- [ ] 3.3 Test remove trigger: create shared function + 5 triggers in DB, desired state declares only 4 triggers →
  migration has exactly 1 `DROP TRIGGER` and no function operations

## 4. Migration executability tests

- [ ] 4.1 Test Shape A migration executes: generate initial-creation migration, run `alembic upgrade head`, inspect
  catalog to verify 5 functions + 5 triggers exist, run `alembic downgrade base`, verify catalog is empty
- [ ] 4.2 Test Shape B migration executes: generate initial-creation migration, run `alembic upgrade head`, inspect
  catalog to verify 1 function + 5 triggers exist, run `alembic downgrade base`, verify catalog is empty
- [ ] 4.3 Test incremental migration executes: generate Shape B baseline migration and upgrade, then generate a second
  migration adding a 6th trigger, upgrade again, verify 6 triggers, downgrade one step, verify 5 triggers remain

## 5. SECURITY DEFINER round-trip tests

- [ ] 5.1 Test SECURITY DEFINER preserved: create a function with `SECURITY DEFINER` in DB, declare identical desired
  state → no operations generated
- [ ] 5.2 Test SECURITY DEFINER addition detected: create a function without `SECURITY DEFINER` in DB, declare desired
  state with `SECURITY DEFINER` → replace operation generated with `SECURITY DEFINER` in definition

## 6. Dependency ordering test

- [ ] 6.1 Test mixed operations ordering: set up DB with functions/triggers for 3 tables, desired state adds 2 new
  tables, modifies one existing function body, and drops triggers for 1 removed table → migration orders as: drop
  triggers, drop functions, create/replace functions, create/replace triggers

## 7. Lint and final verification

- [ ] 7.1 Run `make lint` and `make test` to verify all new tests pass and the module conforms to project style
