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
- **AND** lookup uses `MetaData.tables` keys, so a table under an explicit schema is named `"schema.table"`

#### Scenario: An empty or unsupported event selection is an error

- **WHEN** `AuditSpec.events` is empty, or names an event outside insert, update, and delete
- **THEN** `add_audit_tables()` raises `ValueError`, rather than rendering an `AFTER ... ON` clause with no events

#### Scenario: Excluded columns are not mirrored

- **WHEN** `AuditSpec` excludes a column (e.g. a large `bytea` payload)
- **THEN** the derived audit table has no such column
- **AND** the generated trigger function does not reference it

#### Scenario: An excluded column that no audited table has is an error

- **WHEN** `exclude_columns` names a column absent from every audited table
- **THEN** `add_audit_tables()` raises `ValueError` naming the column, because a typo here would silently audit a column
  the user meant to exclude

#### Scenario: A schema override that makes two derived names collide is an error

- **WHEN** `AuditSpec.schema` places all audit tables in one schema and two audited tables share a name across source
  schemas
- **THEN** `add_audit_tables()` raises `ValueError` naming both source tables and the colliding derived name

### Requirement: Derivation of the audit table

`add_audit_tables(metadata, spec)` SHALL derive, for each audited table, a `Table` named by the source table's name plus
the configured suffix, and attach it to the caller's `MetaData` so Alembic's own comparators manage it.

#### Scenario: Audit table mirrors name and type

- **WHEN** `users(id INTEGER, name TEXT, email TEXT)` is audited
- **THEN** the derived `users_aud` table has columns `id`, `name`, and `email` with the same types

#### Scenario: Mirrored columns carry no constraints

- **WHEN** a source column is a primary key, a foreign key, `NOT NULL`, unique, generated, or has a server default
- **THEN** the mirrored column has none of those: it is nullable, unconstrained, and has no default

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

- **WHEN** the metadata already contains a table named `users_aud` that this generator did not derive (a hand-rolled
  audit table, or any unrelated table)
- **THEN** `add_audit_tables()` raises `ValueError` naming the collision
- **AND** does not adopt it, which would point the generated function at a table whose columns it does not match

#### Scenario: A bookkeeping name colliding with a source column is an error

- **WHEN** an audited table has a column named `aud_at`
- **THEN** `add_audit_tables()` raises `ValueError` naming the table and the column

#### Scenario: Bookkeeping columns are replaced, not extended

- **WHEN** the specification supplies its own `audit_columns` (for example `aud_id`, `aud_action`, and an
  `audit_event_id` foreign key defaulted to a function reading session state)
- **THEN** the derived audit table carries exactly those bookkeeping columns and none of the defaults
- **AND** the generated function still writes only `aud_action` and the mirrored columns

#### Scenario: A replacement set without aud_action is an error

- **WHEN** the specification supplies `audit_columns` containing no `aud_action` column
- **THEN** `add_audit_tables()` raises `ValueError`, because the generated function writes `aud_action` unconditionally
  and its absence would fail at the first write

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

### Requirement: Derivation of the desired function DDL

`add_audit_tables()` SHALL return, for each audited table, a `CREATE OR REPLACE FUNCTION` statement that inserts the
affected row into the audit table, for use by the detection pipeline. The function SHALL be `LANGUAGE plpgsql`,
`SECURITY DEFINER`, with a pinned `search_path`, and SHALL write `TG_OP` into `aud_action` and every mirrored column
from `NEW`, or from `OLD` when `TG_OP` is `'DELETE'`.

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

#### Scenario: One template serves autogenerate and apply time

- **WHEN** the same table shape is rendered from metadata columns and from reflected live columns
- **THEN** both paths produce the same body through one shared rendering function, so the two derivations cannot drift
  from each other

### Requirement: Derivation of the desired trigger DDL

`add_audit_tables()` SHALL return, for each audited table, an `AFTER ... FOR EACH ROW` `CREATE TRIGGER` statement
executing that table's generated function, covering the events named in the specification, for use by the detection
pipeline.

#### Scenario: Trigger covers the configured events

- **WHEN** the specification names insert, update, and delete
- **THEN** the generated trigger is `AFTER INSERT OR UPDATE OR DELETE ... FOR EACH ROW`

#### Scenario: Trigger names no columns

- **WHEN** a column is added to an audited source table
- **THEN** the generated trigger DDL is unchanged
- **AND** only the function DDL differs

### Requirement: Apply-time synchronization operations

The module SHALL provide `sync_audit` and `drop_audit` Alembic operations. `sync_audit` SHALL derive the function body
at apply time from the live catalog and execute it, together with the trigger DDL when the live trigger is absent or
differs. `drop_audit` SHALL drop the trigger and the function. Both SHALL carry only scalars (table names, schema,
function and trigger names, events, and the action column name), never a function body or column list.

#### Scenario: Mirrored columns are the live intersection

- **WHEN** `sync_audit` runs
- **THEN** it reflects the source table and the audit table from its connection
- **AND** writes the columns present in both tables, in the source table's order
- **AND** treats columns present only in the audit table as bookkeeping, writing none of them except the action column

#### Scenario: A retained history column goes NULL rather than breaking the trigger

- **WHEN** an audit column's source column was dropped but the audit column was retained via `include_object`
- **THEN** the synchronized function does not reference it
- **AND** later audit rows record NULL for it

#### Scenario: A lenient sync skips when the audit table is absent

- **WHEN** `sync_audit` runs with `missing_ok=True` and the audit table does not exist
- **THEN** it does nothing and does not raise

#### Scenario: A strict sync fails loudly when the audit table is absent

- **WHEN** `sync_audit` runs without `missing_ok` and the audit table does not exist
- **THEN** the migration fails with an error naming the missing table

#### Scenario: Reverses are assigned by diff action

- **WHEN** autogenerate emits a synchronization that creates the audit function and trigger
- **THEN** its reverse is `drop_audit`
- **AND** the reverse of a body-replacing `sync_audit` is `sync_audit`
- **AND** the reverse of `drop_audit` is a creating `sync_audit`

#### Scenario: Offline mode is a loud error

- **WHEN** `sync_audit` or `drop_audit` is invoked under `alembic upgrade --sql`
- **THEN** the operation raises an error naming the offline limitation, rather than emitting wrong or empty SQL

### Requirement: Integration with the existing pipeline

Detection SHALL use machinery that already exists: the audit `Table` goes to Alembic's table and column comparators, and
the derived DDL goes to the `pg_functions` and `pg_triggers` desired-state keys as marked strings. Rendering SHALL
replace literal DDL with `sync_audit` / `drop_audit` brackets for marked entries only. Migrations SHALL contain no audit
function body.

#### Scenario: First run creates the whole pattern without a stored body

- **WHEN** autogenerate runs against a database with none of the audit objects present
- **THEN** the migration contains `op.create_table("users_aud", ...)` from Alembic
- **AND** `sync_audit` operations from this library, bracketing Alembic's operations
- **AND** no `CREATE FUNCTION` or `CREATE TRIGGER` text appears in the migration file

#### Scenario: Adding a source column updates table and function together

- **WHEN** a column is added to an audited model and autogenerate runs
- **THEN** the migration contains `op.add_column("users", ...)` and `op.add_column("users_aud", ...)`
- **AND** a `sync_audit` bracket, with a lenient operation before the column operations and a strict one after them
- **AND** the reversed `downgrade()` also ends with a synchronization that runs after its column drops

#### Scenario: Unchanged models produce no operations

- **WHEN** the database already matches the derived state
- **THEN** autogenerate produces a migration with no operations in `upgrade()`

#### Scenario: Removing a table from the specification removes its objects

- **WHEN** an audited table is dropped from `AuditSpec.tables` and its generated DDL is spliced into `pg_functions` and
  `pg_triggers` as before
- **THEN** the migration drops that table's trigger and function via `drop_audit`
- **AND** drops the audit table, because it is no longer in the metadata
- **AND** the reversed `downgrade()` recreates the table first and the function and trigger after it

#### Scenario: Generated DDL composes with hand-written DDL

- **WHEN** `pg_functions=[*PG_FUNCTIONS, *audit.functions]` is configured
- **THEN** both the user's own functions and the generated ones are declared
- **AND** neither set is dropped as undeclared
- **AND** the user's own functions still render as literal DDL

### Requirement: Executability against PostgreSQL

Generated audit migrations SHALL be executable against a live PostgreSQL database, and the resulting triggers SHALL
record the rows they are meant to record, after upgrades and after downgrades.

#### Scenario: The trail is written

- **WHEN** a generated audit migration is applied and a row is inserted, updated, and deleted in the source table
- **THEN** the audit table holds three rows with `aud_action` of `'INSERT'`, `'UPDATE'`, and `'DELETE'`
- **AND** each holds the affected row's values and a populated `aud_at` and `aud_actor`

#### Scenario: Round-trip through an added column

- **WHEN** a baseline audit migration is applied, a column is added to the model, the follow-up migration is generated
  and applied, and then downgraded
- **THEN** after the upgrade a write records the new column's value
- **AND** after the downgrade a write succeeds against the re-derived function and the pre-existing audit rows remain
- **AND** the post-downgrade function equals a fresh derivation for the baseline model

#### Scenario: Round-trip through a dropped column

- **WHEN** a column is dropped from an audited model, the migration is generated and applied, and then downgraded
- **THEN** after the upgrade a write succeeds and the function no longer references the column
- **AND** after the downgrade a write records the restored column's value

#### Scenario: Merged branches converge

- **WHEN** two branches each add a different column to the same audited model, each generates its own migration against
  a database lacking the other's column, and both migrations are applied after a merge
- **THEN** the final function writes both columns, because the last synchronization derives from a table that has both
- **AND** a subsequent autogenerate run against the merged model produces no operations
