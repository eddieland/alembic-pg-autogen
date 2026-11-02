## ADDED Requirements

### Requirement: pg_grants configuration key

The comparator SHALL read declared grants from `autogen_context.opts["pg_grants"]`. The key SHALL follow the rules of
the other desired-state keys. An absent key and `IGNORED` leave grants unmanaged. An empty sequence declares that no
grants exist. The key SHALL accept `SQLCreatable` objects, as the other keys do.

#### Scenario: Only grants managed

- **WHEN** `context.configure()` is called with `pg_grants=[...]` and no other `pg_*` key
- **THEN** the comparator inspects and compares grants
- **AND** it logs functions, triggers, and views as unmanaged

#### Scenario: Grants unmanaged

- **WHEN** `pg_grants` is absent or `IGNORED`
- **THEN** the comparator does not read privileges and emits no grant operation

#### Scenario: Misspelled key

- **WHEN** the configuration holds `pg_grant`
- **THEN** the comparator warns that the key is unrecognized and suggests `pg_grants`

### Requirement: Position of grant operations

The comparator SHALL insert `RevokePrivilegesOp` and `AlterGrantOptionOp` instances at the start of `upgrade_ops.ops`.
It SHALL append `GrantPrivilegesOp` instances after the function, view, and trigger operations.

#### Scenario: Grant on a new table

- **WHEN** a new table in `target_metadata` has a declared grant
- **THEN** `upgrade()` holds the `GRANT` after `op.create_table()`
- **AND** `downgrade()` holds the `REVOKE` before `op.drop_table()`

#### Scenario: Grant on a dropped table

- **WHEN** a table leaves `target_metadata` and its grant leaves `pg_grants`
- **THEN** `upgrade()` holds the `REVOKE` before `op.drop_table()`

#### Scenario: Grant on a new view

- **WHEN** `pg_views` declares a new view and `pg_grants` declares a grant on it
- **THEN** `upgrade()` holds the `GRANT` after the `CREATE OR REPLACE VIEW`
