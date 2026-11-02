## ADDED Requirements

### Requirement: Compare declared grants with the catalog

When `pg_grants` holds a sequence, the comparator SHALL compare the declared table and schema privileges with the
privileges in the catalog. It SHALL emit operations for each difference. PostgreSQL features targeted: `GRANT`,
`REVOKE`, `REVOKE GRANT OPTION FOR`, `pg_class.relacl`, `pg_namespace.nspacl`, `aclexplode()`, and `pg_init_privs`.

#### Scenario: Missing privilege

- **WHEN** `pg_grants=["GRANT SELECT, INSERT ON orders TO app"]` and `app` holds no privilege on `orders`
- **THEN** `upgrade()` contains `op.execute('GRANT SELECT, INSERT ON TABLE public.orders TO app')`
- **AND** `downgrade()` contains `op.execute('REVOKE SELECT, INSERT ON TABLE public.orders FROM app')`

#### Scenario: Undeclared privilege

- **WHEN** `app` holds `SELECT` and `DELETE` on `orders` and `pg_grants=["GRANT SELECT ON orders TO app"]`
- **THEN** `upgrade()` contains `REVOKE DELETE ON TABLE public.orders FROM app`
- **AND** `upgrade()` contains no statement for `SELECT`

#### Scenario: Matching privileges

- **WHEN** the declared grants equal the catalog grants, grant options included
- **THEN** the comparator emits no grant operation

#### Scenario: Grant option added

- **WHEN** `app` holds `SELECT` on `orders` without the grant option and the declaration has `WITH GRANT OPTION`
- **THEN** `upgrade()` contains `GRANT SELECT ON TABLE public.orders TO app WITH GRANT OPTION`
- **AND** `downgrade()` contains `REVOKE GRANT OPTION FOR SELECT ON TABLE public.orders FROM app`

#### Scenario: Grant option removed

- **WHEN** `app` holds `SELECT` with the grant option and the declaration has no `WITH GRANT OPTION`
- **THEN** `upgrade()` contains `REVOKE GRANT OPTION FOR SELECT ON TABLE public.orders FROM app`
- **AND** `upgrade()` contains no `REVOKE SELECT`

#### Scenario: Schema grant

- **WHEN** `pg_grants=["GRANT USAGE ON SCHEMA sales TO app"]` and `app` has no privilege on `sales`
- **THEN** `upgrade()` contains `GRANT USAGE ON SCHEMA sales TO app`

### Requirement: Privileges that PostgreSQL added are unmanaged unless declared

The comparator SHALL remove these privileges from the current grants when `pg_grants` does not declare them: each
privilege that `pg_init_privs` records for an object, each privilege that `pg_default_acl` gives to new objects of the
owner, and the `initdb` defaults of `PUBLIC` on the `public` schema (`USAGE`, and `CREATE` before PostgreSQL 15). The
match SHALL use the identity of the grant and ignore `grantable`. The desired grants SHALL keep every declared
privilege.

#### Scenario: Public schema keeps the USAGE privilege of PUBLIC

- **WHEN** `pg_grants=[]` and the inspected schemas include `public`
- **THEN** the comparator does not emit `REVOKE USAGE ON SCHEMA public FROM PUBLIC`

#### Scenario: Recreated public schema

- **WHEN** the script ran `DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT USAGE ON SCHEMA public TO PUBLIC`
- **AND** `pg_grants=[]`
- **THEN** the comparator does not emit `REVOKE USAGE ON SCHEMA public FROM PUBLIC`

#### Scenario: CREATE on the public schema on PostgreSQL 15 and later

- **WHEN** `PUBLIC` holds `CREATE` on `public`, the server is PostgreSQL 15 or later, and `pg_grants=[]`
- **THEN** the comparator emits `REVOKE CREATE ON SCHEMA public FROM PUBLIC`

#### Scenario: Default privileges

- **WHEN** the owner ran `ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO rdr` and created a table
- **AND** `pg_grants=[]`
- **THEN** the comparator does not emit `REVOKE SELECT ... FROM rdr` for that table

#### Scenario: Declared grant on a table older than the default ACL

- **WHEN** a table exists, the owner then ran `ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO rdr`, and `pg_grants`
  declares `GRANT SELECT` on that table to `rdr`
- **THEN** the comparator emits that `GRANT`

#### Scenario: Declared grant option on a default privilege

- **WHEN** `rdr` holds `SELECT` from the default ACL and `pg_grants` declares it `WITH GRANT OPTION`
- **THEN** the comparator emits `GRANT SELECT ... WITH GRANT OPTION`

#### Scenario: Declared initial privilege

- **WHEN** `pg_grants=["GRANT USAGE ON SCHEMA public TO PUBLIC"]` and the privilege is present
- **THEN** the comparator emits no operation for that grant

### Requirement: Grant operations

The module `alembic_pg_autogen.ops` SHALL provide `GrantPrivilegesOp`, `RevokePrivilegesOp`, and `AlterGrantOptionOp`.
Each SHALL hold `grants`, a tuple of `GrantInfo` with one object, one grantee, and one `grantable` value.

#### Scenario: Group check

- **WHEN** an operation is constructed with an empty sequence, or with grants that differ in object, grantee, or
  `grantable`
- **THEN** the constructor raises `ValueError`

#### Scenario: Reverse operations

- **WHEN** `reverse()` is called
- **THEN** `GrantPrivilegesOp` returns a `RevokePrivilegesOp` with the same grants
- **AND** `RevokePrivilegesOp` returns a `GrantPrivilegesOp` with the same grants, grant option included
- **AND** `AlterGrantOptionOp` returns an `AlterGrantOptionOp` with each `grantable` flag inverted

### Requirement: Grant rendering

The renderers SHALL emit one `op.execute()` call per operation. They SHALL quote identifiers with the dialect's
identifier preparer. They SHALL write privileges in the order of the PostgreSQL documentation.

#### Scenario: PUBLIC pseudo-role

- **WHEN** a grant has the grantee `public`
- **THEN** the statement uses the keyword `PUBLIC`

#### Scenario: Role named PUBLIC in uppercase

- **WHEN** a grant has the grantee `PUBLIC`
- **THEN** the statement uses the quoted identifier `"PUBLIC"`

#### Scenario: ALL privileges

- **WHEN** the declaration uses `GRANT ALL` for a target and the group holds every privilege of `ALL` on the
  autogenerate server
- **THEN** the statement uses `ALL` in place of the privilege list

#### Scenario: Privileges named one by one

- **WHEN** the declaration names every privilege of the object type one by one
- **THEN** the statement lists the privileges and does not use `ALL`

#### Scenario: Line break in an implicit revoke

- **WHEN** a dropped table has a name with a line break
- **THEN** the rendered comment holds the statement through `repr()`, and the revision compiles

#### Scenario: Percent sign in an identifier

- **WHEN** the table is `50%off` and the connection uses psycopg
- **THEN** the statement holds `"50%off"`, not `"50%%off"`

#### Scenario: Identifiers that need quotes

- **WHEN** the schema is `My Schema`, the table is `Order Items`, and the grantee is `Report Reader`
- **THEN** the statement is `REVOKE SELECT ON TABLE "My Schema"."Order Items" FROM "Report Reader"`

### Requirement: Supported GRANT forms

`canonicalize_grants()` SHALL accept `GRANT` statements on named tables and named schemas. It SHALL raise `ValueError`
for any other statement.

#### Scenario: Rejected forms

- **WHEN** a declared string holds `REVOKE`, a statement that is not `GRANT`, `GRANT ... ON ALL TABLES IN SCHEMA`,
  `GRANT ... ON SEQUENCE`, `GRANT ... ON FUNCTION`, `GRANTED BY`, `WITH GRANT OPTION` for `PUBLIC`, a column privilege,
  role membership, a privilege that the object type does not have, or a name that finds a sequence
- **THEN** `canonicalize_grants()` raises `ValueError` with the statement in the message
