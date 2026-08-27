## ADDED Requirements

### Requirement: Audit specification

The module SHALL provide an `AuditSpec` configuration object naming the tables to audit and the shape of the derived
objects. It SHALL expose at least `tables`, `suffix` (default `"_aud"`), `schema`, `events` (default insert, update, and
delete), `exclude_columns`, and the audit bookkeeping columns. `events` SHALL be validated as a non-empty subset of the
supported row events before any DDL is rendered.

#### Scenario: Table names select what is audited

- **WHEN** `AuditSpec(tables=["users", "orders"])` is used with a `MetaData` also containing `products`
- **THEN** audit objects are derived for `users` and `orders` only
- **AND** `products` is untouched

#### Scenario: A named table absent from the metadata is an error

- **WHEN** `AuditSpec(tables=["nonexistent"])` is used
- **THEN** `add_audit_tables()` raises `ValueError` naming the missing table

#### Scenario: An empty or unsupported event selection is an error

- **WHEN** `AuditSpec.events` is empty, or names an event outside insert, update, and delete
- **THEN** `add_audit_tables()` raises `ValueError`, rather than rendering an `AFTER ... ON` clause with no events

#### Scenario: Excluded columns are not mirrored

- **WHEN** `AuditSpec` excludes a column (e.g. a large `bytea` payload)
- **THEN** the derived audit table has no such column
- **AND** the generated trigger function does not reference it

### Requirement: Derivation of the audit table

`add_audit_tables(metadata, spec)` SHALL derive, for each audited table, a `Table` named by the source table's name plus
the configured suffix, and attach it to the caller's `MetaData` so Alembic's own comparators manage it.

#### Scenario: Audit table mirrors name and type

- **WHEN** `users(id INTEGER, name TEXT, email TEXT)` is audited
- **THEN** the derived `users_aud` table has columns `id`, `name`, and `email` with the same types

#### Scenario: Mirrored columns carry no constraints

- **WHEN** a source column is a primary key, a foreign key, `NOT NULL`, unique, or has a server default
- **THEN** the mirrored column has none of those — it is nullable, unconstrained, and has no default

#### Scenario: Bookkeeping columns are added

- **WHEN** an audit table is derived with the default specification
- **THEN** it has `aud_id` (identity primary key), `aud_action`, `aud_at` (defaulting to `now()`), and `aud_actor`
  (defaulting to `session_user`)

#### Scenario: The actor column survives the privilege switch

- **WHEN** a role other than the trigger function's owner writes to an audited table
- **THEN** the recorded `aud_actor` is that writer's login role, not the function owner
- **AND** the default is `session_user`, because `current_user` inside a `SECURITY DEFINER` function is the owner and
  would record the same value for every writer

#### Scenario: Source primary key is indexed

- **WHEN** an audit table is derived for a table with primary key `id`
- **THEN** the audit table carries a non-unique index on `id`
- **AND** an index on `aud_at`

#### Scenario: A keyless source table omits the lookup index

- **WHEN** an audit table is derived for a source table with no primary key columns
- **THEN** the audit table carries the `aud_at` index and no source-row lookup index
- **AND** derivation succeeds rather than emitting an index over an empty column list

#### Scenario: Enum types are shared, not duplicated

- **WHEN** a source column uses a named `sa.Enum`
- **THEN** the mirrored column uses the same type object, so no second PostgreSQL enum type is generated

#### Scenario: Attaching is idempotent

- **WHEN** `add_audit_tables()` is called twice on the same `MetaData` with the same specification
- **THEN** the second call does not raise
- **AND** the metadata contains one audit table per audited table
- **AND** each derived table is marked as generated, so the second call recognizes its own work

#### Scenario: An unrelated table occupying the derived name is an error

- **WHEN** the metadata already contains a table named `users_aud` that this generator did not derive — a hand-rolled
  audit table, or any unrelated table
- **THEN** `add_audit_tables()` raises `ValueError` naming the collision
- **AND** does not adopt it, which would point the generated function at a table whose columns it does not match

#### Scenario: A bookkeeping name colliding with a source column is an error

- **WHEN** an audited table has a column named `aud_at`
- **THEN** `add_audit_tables()` raises `ValueError` naming the table and the column

#### Scenario: Bookkeeping columns are replaced, not extended

- **WHEN** the specification supplies its own `audit_columns` — for example `aud_id`, `aud_action`, and an
  `audit_event_id` foreign key defaulted to a function reading session state
- **THEN** the derived audit table carries exactly those bookkeeping columns and none of the defaults
- **AND** the generated function still writes only `aud_action` and the mirrored columns

#### Scenario: Bookkeeping columns are built per table

- **WHEN** more than one table is audited
- **THEN** each derived audit table receives its own `Column` objects, because a SQLAlchemy `Column` cannot be attached
  to two `Table` objects
- **AND** `audit_columns` is therefore a factory invoked once per audited table, not a shared sequence of instances

#### Scenario: Bookkeeping constraints are preserved

- **WHEN** a bookkeeping column declares a foreign key or `NOT NULL`
- **THEN** the derived audit table keeps them, because the constraint stripping applies to mirrored columns only

#### Scenario: A bookkeeping column that cannot populate itself is an error

- **WHEN** the specification supplies a bookkeeping column that is `NOT NULL`, is not `aud_action`, and has no server
  default
- **THEN** `add_audit_tables()` raises `ValueError`, because the generated function writes only `aud_action`

### Requirement: Derivation of the trigger function

`add_audit_tables()` SHALL return, for each audited table, a `CREATE OR REPLACE FUNCTION` statement that inserts the
affected row into the audit table. The function SHALL be `LANGUAGE plpgsql`, `SECURITY DEFINER`, with a pinned
`search_path`, and SHALL write `TG_OP` into `aud_action` and every mirrored column from `NEW` — or from `OLD` when
`TG_OP` is `'DELETE'`.

#### Scenario: Function enumerates the mirrored columns

- **WHEN** a function is generated for `users(id, name, email)`
- **THEN** its body inserts `aud_action`, `id`, `name`, and `email` into `users_aud`

#### Scenario: Deletes record the old row

- **WHEN** the generated function runs for a `DELETE`
- **THEN** the audit row holds the `OLD` values and `aud_action` is `'DELETE'`

#### Scenario: Bookkeeping columns beyond aud_action are not written

- **WHEN** a function is generated
- **THEN** its `INSERT` column list contains `aud_action` and the mirrored columns only, leaving `aud_id`, `aud_at`, and
  `aud_actor` to their defaults

#### Scenario: Identifiers are quoted

- **WHEN** an audited table has a column named `order` or `Name`
- **THEN** the generated body quotes it, and the DDL executes against PostgreSQL without a syntax error

#### Scenario: Generation is deterministic

- **WHEN** `add_audit_tables()` is called twice with the same metadata and specification
- **THEN** the generated DDL strings are byte-identical, so an unchanged model produces no diff

### Requirement: Derivation of the trigger

`add_audit_tables()` SHALL return, for each audited table, an `AFTER ... FOR EACH ROW` `CREATE TRIGGER` statement
executing that table's generated function, covering the events named in the specification.

#### Scenario: Trigger covers the configured events

- **WHEN** the specification names insert, update, and delete
- **THEN** the generated trigger is `AFTER INSERT OR UPDATE OR DELETE ... FOR EACH ROW`

#### Scenario: Trigger names no columns

- **WHEN** a column is added to an audited source table
- **THEN** the generated trigger DDL is unchanged
- **AND** only the function DDL differs

### Requirement: Integration with the existing pipeline

The derived objects SHALL be consumed by machinery that already exists: the audit `Table` by Alembic's table and column
comparators, and the generated DDL by the `pg_functions` and `pg_triggers` desired-state keys. The change SHALL add no
comparator, operation type, or renderer.

#### Scenario: First run creates the whole pattern

- **WHEN** autogenerate runs against a database with none of the audit objects present
- **THEN** the migration contains `op.create_table("users_aud", ...)` from Alembic
- **AND** a `CREATE FUNCTION` and a `CREATE TRIGGER` from this library

#### Scenario: Adding a source column updates table and function together

- **WHEN** a column is added to an audited model and autogenerate runs
- **THEN** the migration contains `op.add_column("users", ...)` and `op.add_column("users_aud", ...)`
- **AND** a function replacement whose new body references the added column
- **AND** the audit table's `add_column` precedes the function replacement in `upgrade()`

#### Scenario: Downgrade restores the catalog's own body

- **WHEN** a migration replacing an audit function is generated
- **THEN** the `downgrade()` body is the definition read back from `pg_proc`, not a copy supplied by the author
- **AND** running `upgrade()` then `downgrade()` leaves the function byte-identical to its pre-upgrade definition

#### Scenario: Unchanged models produce no operations

- **WHEN** the database already matches the derived state
- **THEN** autogenerate produces a migration with no operations in `upgrade()`

#### Scenario: Removing a table from the specification removes its objects

- **WHEN** an audited table is dropped from `AuditSpec.tables` and its generated DDL is spliced into `pg_functions` and
  `pg_triggers` as before
- **THEN** the migration drops that table's trigger and function
- **AND** drops the audit table, because it is no longer in the metadata

#### Scenario: Generated DDL composes with hand-written DDL

- **WHEN** `pg_functions=[*PG_FUNCTIONS, *audit.functions]` is configured
- **THEN** both the user's own functions and the generated ones are declared
- **AND** neither set is dropped as undeclared

### Requirement: Executability against PostgreSQL

Generated audit migrations SHALL be executable against a live PostgreSQL database, and the resulting triggers SHALL
record the rows they are meant to record.

#### Scenario: The trail is written

- **WHEN** a generated audit migration is applied and a row is inserted, updated, and deleted in the source table
- **THEN** the audit table holds three rows with `aud_action` of `'INSERT'`, `'UPDATE'`, and `'DELETE'`
- **AND** each holds the affected row's values and a populated `aud_at` and `aud_actor`

#### Scenario: Round-trip through an added column

- **WHEN** a baseline audit migration is applied, a column is added to the model, the follow-up migration is generated
  and applied, and then downgraded
- **THEN** after the upgrade a write records the new column's value
- **AND** after the downgrade a write succeeds against the restored function and the pre-existing audit rows remain
