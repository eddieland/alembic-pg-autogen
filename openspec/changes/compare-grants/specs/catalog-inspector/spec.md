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

The module SHALL provide `inspect_grants(conn, schemas=None, *, source="current")`. It SHALL read an ACL for each
relation with `relkind` in `('r', 'p', 'v', 'm', 'f')` and for each schema, expanded through `aclexplode()`. It SHALL
return a sequence of `GrantInfo` sorted by identity. *source* SHALL select the ACL: `"current"` reads `pg_class.relacl`
and `pg_namespace.nspacl`, `"initial"` reads `pg_init_privs`, and `"default"` reads `pg_default_acl` for the owner of
each object. Any other value SHALL raise `ValueError`.

#### Scenario: Owner privileges excluded

- **WHEN** a table owner holds every privilege and another role holds `SELECT`
- **THEN** the result holds only the `SELECT` row of the other role

#### Scenario: Sequences and column privileges excluded

- **WHEN** a role holds `USAGE` on a sequence and `SELECT (col)` on a table
- **THEN** the result is empty

#### Scenario: Extension objects included

- **WHEN** an extension owns a table (`pg_depend.deptype = 'e'`) and a role holds `SELECT` on it
- **THEN** the result holds that row, so a declared grant on the table can match it

#### Scenario: Several grantors

- **WHEN** two grantors give `SELECT` on one table to one role, and only one of them uses `WITH GRANT OPTION`
- **THEN** the result holds one row with `grantable=True`

#### Scenario: Initial privileges

- **WHEN** `source="initial"`
- **THEN** the query reads the ACL in `pg_init_privs` for each object in place of the live ACL
- **AND** the `public` schema gives a `USAGE` row for the grantee `public`

#### Scenario: Default privileges

- **WHEN** the owner ran `ALTER DEFAULT PRIVILEGES GRANT INSERT ON TABLES TO r` and
  `ALTER DEFAULT PRIVILEGES IN SCHEMA s GRANT SELECT ON TABLES TO r`, and then created `s.t`
- **AND** `source="default"`
- **THEN** the result holds `INSERT` and `SELECT` rows for `r` on `s.t`
