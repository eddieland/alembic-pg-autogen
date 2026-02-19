## ADDED Requirements

### Requirement: Shape A audit pattern — per-table function and trigger

The test suite SHALL exercise the full autogenerate pipeline with the Shape A audit pattern: one dedicated audit
function and one trigger per table. Tests SHALL use at least 5 tables (e.g., `users`, `orders`, `payments`, `products`,
`audit_log`). Audit functions SHALL use `SECURITY DEFINER` and PL/pgSQL bodies that insert into a shared audit table.

#### Scenario: Initial creation of Shape A audit objects

- **WHEN** no audit functions or triggers exist in the database AND the desired state declares 5 per-table audit
  functions and 5 triggers
- **THEN** autogenerate produces a migration with 5 `CREATE FUNCTION` and 5 `CREATE TRIGGER` operations AND all function
  creates appear before all trigger creates in the upgrade path

#### Scenario: Shape A no-op when state matches

- **WHEN** the database already contains the exact audit functions and triggers matching the desired state
- **THEN** autogenerate produces a migration with no operations in `upgrade()`

#### Scenario: Shape A function body modification

- **WHEN** the database contains existing audit functions and triggers AND the desired state changes the body of one
  audit function (e.g., adding a column to the audit record)
- **THEN** autogenerate produces a migration with exactly one `REPLACE` operation for the modified function AND no
  trigger operations

### Requirement: Shape B audit pattern — shared function with per-table triggers

The test suite SHALL exercise the full autogenerate pipeline with the Shape B audit pattern: one shared audit function
using `TG_TABLE_NAME` and `row_to_json(NEW)`, plus one trigger per table referencing that shared function. The shared
function SHALL use `SECURITY DEFINER`.

#### Scenario: Initial creation of Shape B audit objects

- **WHEN** no audit functions or triggers exist in the database AND the desired state declares 1 shared audit function
  and 5 triggers
- **THEN** autogenerate produces a migration with 1 `CREATE FUNCTION` and 5 `CREATE TRIGGER` operations AND the function
  create appears before all trigger creates

#### Scenario: Shape B add trigger for new table

- **WHEN** the database already contains the shared audit function and triggers for 5 tables AND the desired state adds
  a trigger for a 6th table
- **THEN** autogenerate produces a migration with exactly 1 `CREATE TRIGGER` operation for the new table AND no function
  operations

#### Scenario: Shape B remove trigger for dropped table

- **WHEN** the database contains the shared audit function and triggers for 5 tables AND the desired state removes the
  trigger for one table (declaring only 4 triggers)
- **THEN** autogenerate produces a migration with exactly 1 `DROP TRIGGER` operation AND no function operations

### Requirement: Migration executability

Generated migrations SHALL be executable against a live PostgreSQL database. Tests SHALL verify that running `upgrade()`
creates the expected objects and running `downgrade()` removes them.

#### Scenario: Shape A migration executes successfully

- **WHEN** a Shape A initial-creation migration is generated
- **THEN** running `alembic upgrade head` creates all 5 audit functions and 5 triggers in the database (verified via
  catalog inspection) AND running `alembic downgrade base` removes all of them (verified via catalog inspection
  returning empty results)

#### Scenario: Shape B migration executes successfully

- **WHEN** a Shape B initial-creation migration is generated
- **THEN** running `alembic upgrade head` creates the shared audit function and all 5 triggers in the database AND
  running `alembic downgrade base` removes all of them

#### Scenario: Incremental migration executes successfully

- **WHEN** a Shape B add-trigger migration is generated on top of an existing baseline migration
- **THEN** running `alembic upgrade head` (from the baseline) creates only the new trigger AND running
  `alembic downgrade -1` removes only the new trigger while preserving the baseline objects

### Requirement: SECURITY DEFINER round-trip

Audit functions declared with `SECURITY DEFINER` SHALL canonicalize and diff correctly through the full pipeline.

#### Scenario: SECURITY DEFINER attribute preserved in canonicalization

- **WHEN** a function is declared with `SECURITY DEFINER` AND the database contains the same function with
  `SECURITY DEFINER`
- **THEN** autogenerate produces no operations (the attribute is not lost or mismatched during canonicalization)

#### Scenario: SECURITY DEFINER addition detected as change

- **WHEN** the database contains a function without `SECURITY DEFINER` AND the desired state adds `SECURITY DEFINER`
- **THEN** autogenerate produces a `REPLACE` operation whose definition includes `SECURITY DEFINER`

### Requirement: Dependency ordering under realistic conditions

The autogenerate pipeline SHALL produce correctly ordered operations when handling mixed create, replace, and drop
operations across functions and triggers in a single migration.

#### Scenario: Mixed operations maintain dependency order

- **WHEN** the desired state simultaneously creates new audit functions/triggers for added tables, modifies an existing
  audit function body, and drops triggers for removed tables
- **THEN** the generated migration orders operations as: drop triggers, drop functions, create/replace functions,
  create/replace triggers
