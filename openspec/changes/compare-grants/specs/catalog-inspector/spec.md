## ADDED Requirements

### Requirement: GrantInfo type

The module SHALL provide a `GrantInfo` NamedTuple. It holds one privilege of one role on one table or schema.

#### Scenario: GrantInfo fields

- **WHEN** a `GrantInfo` instance is created
- **THEN** it has these fields:
  - `schema` (`str`): the namespace of the table, or the schema itself for a schema grant
  - `object_type` (`str`): `"TABLE"` or `"SCHEMA"`
  - `name` (`str`): the table name, or the schema name for a schema grant
  - `grantee` (`str`): the role name, or `"public"` for the `PUBLIC` pseudo-role
  - `privilege` (`str`): the privilege name in uppercase, from `aclexplode().privilege_type`
  - `grantable` (`bool`, default `False`): `True` when the role holds the privilege `WITH GRANT OPTION`

#### Scenario: GrantInfo identity

- **WHEN** two `GrantInfo` instances have equal `info[:-1]`
- **THEN** they are the same privilege, and only `grantable` can differ

### Requirement: Read table and schema privileges from the PostgreSQL catalog in one query

The module SHALL provide `inspect_grants(conn, schemas=None, *, initial=False)`. It SHALL read `pg_class.relacl` for
relations with `relkind` in `('r', 'p', 'v', 'm', 'f')` and `pg_namespace.nspacl` for schemas, expanded through
`aclexplode()`. It SHALL return a sequence of `GrantInfo` sorted by identity.

#### Scenario: Owner privileges excluded

- **WHEN** a table owner holds every privilege and another role holds `SELECT`
- **THEN** the result holds only the `SELECT` row of the other role

#### Scenario: Sequences and column privileges excluded

- **WHEN** a role holds `USAGE` on a sequence and `SELECT (col)` on a table
- **THEN** the result is empty

#### Scenario: Extension objects excluded

- **WHEN** an extension owns a table or a schema (`pg_depend.deptype = 'e'`)
- **THEN** the result holds no row for that object

#### Scenario: Several grantors

- **WHEN** two grantors give `SELECT` on one table to one role, and only one of them uses `WITH GRANT OPTION`
- **THEN** the result holds one row with `grantable=True`

#### Scenario: Initial privileges

- **WHEN** `initial=True`
- **THEN** the query reads the ACL in `pg_init_privs` for each object in place of the live ACL
- **AND** the `public` schema gives a `USAGE` row for the grantee `public`
