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

### Requirement: Initial privileges are unmanaged

The comparator SHALL remove each privilege that `pg_init_privs` records for an object from both the current and the
desired grants. The match SHALL use the identity of the grant and ignore `grantable`.

#### Scenario: Public schema keeps the USAGE privilege of PUBLIC

- **WHEN** `pg_grants=[]` and the inspected schemas include `public`
- **THEN** the comparator does not emit `REVOKE USAGE ON SCHEMA public FROM PUBLIC`

#### Scenario: Declared initial privilege

- **WHEN** `pg_grants=["GRANT USAGE ON SCHEMA public TO PUBLIC"]`
- **THEN** the comparator emits no operation for that grant, whether or not the privilege is present

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

#### Scenario: Identifiers that need quotes

- **WHEN** the schema is `My Schema`, the table is `Order Items`, and the grantee is `Report Reader`
- **THEN** the statement is `REVOKE SELECT ON TABLE "My Schema"."Order Items" FROM "Report Reader"`

### Requirement: Supported GRANT forms

`canonicalize_grants()` SHALL accept `GRANT` statements on named tables and named schemas. It SHALL raise `ValueError`
for any other statement.

#### Scenario: Rejected forms

- **WHEN** a declared string holds `REVOKE`, a statement that is not `GRANT`, `GRANT ... ON ALL TABLES IN SCHEMA`,
  `GRANT ... ON SEQUENCE`, `GRANT ... ON FUNCTION`, a column privilege, role membership, or a privilege that the object
  type does not have
- **THEN** `canonicalize_grants()` raises `ValueError` with the statement in the message
