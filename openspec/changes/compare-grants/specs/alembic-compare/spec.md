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
- **THEN** `upgrade()` holds no `REVOKE` statement for that table, only a `pass` line with a comment
- **AND** the upgrade succeeds when one role passed the privilege on to another
- **AND** `downgrade()` grants the privileges again after `op.create_table()`

#### Scenario: Declared grant on a dropped table

- **WHEN** a table leaves `target_metadata` and `pg_grants` still declares a grant on it
- **THEN** the comparator logs a warning that names the table

#### Scenario: Grant on a new view

- **WHEN** `pg_views` declares a new view and `pg_grants` declares a grant on it
- **THEN** `upgrade()` holds the `GRANT` after the `CREATE OR REPLACE VIEW`

### Requirement: Alembic filters and the version table

The comparator SHALL skip grants on the Alembic version table. It SHALL apply `include_name` to each table with type
`"table"` and to each schema with type `"schema"`, with `None` for the default schema. It SHALL apply `include_object`
to each table with type `"table"`. It SHALL log a warning for each declared object that these rules remove.

#### Scenario: include_object excludes a table

- **WHEN** a role holds `SELECT` on table `ex`, `pg_grants=[]`, and `include_object` returns `False` for `ex`
- **THEN** the comparator emits no `REVOKE` for `ex`

#### Scenario: Default schema reaches include_name as None

- **WHEN** `include_name` keeps only the schemas in `[None, "s1"]` and `pg_grants` declares `USAGE` on the default
  schema
- **THEN** the comparator emits that `GRANT`

#### Scenario: Schema-qualified metadata table

- **WHEN** `target_metadata` holds `Table("o", md, schema="public")` and `public` is the default schema
- **THEN** autogenerate does not fail, and `include_object` receives that `Table` with `reflected=False`

#### Scenario: Version table

- **WHEN** a role holds `SELECT` on `alembic_version` and `pg_grants=[]`
- **THEN** the comparator emits no `REVOKE` for `alembic_version`

### Requirement: Declared grants outside the compared schemas

The comparator SHALL log one warning for each declared object whose schema autogenerate does not compare. The warning
SHALL name the object and mention `include_schemas`.

#### Scenario: Schema not compared

- **WHEN** `pg_grants` declares `GRANT SELECT ON sales.o TO app` and `include_schemas` is not set
- **THEN** the comparator logs a warning that names `TABLE sales.o`
- **AND** it emits no operation for that grant
