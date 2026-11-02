## ADDED Requirements

### Requirement: Parse GRANT statements into catalog form

The module SHALL provide `canonicalize_grants(conn, ddl)`. It SHALL parse each statement with postgast and return a
sequence of `GrantInfo` sorted by identity. It SHALL NOT execute the statements.

#### Scenario: Expansion

- **WHEN** `ddl` is `["GRANT SELECT, INSERT ON a, b TO x, PUBLIC"]`
- **THEN** the result holds eight `GrantInfo` rows, one per table, grantee, and privilege
- **AND** the grantee of `PUBLIC` is `"public"`

#### Scenario: Equal to the catalog after execution

- **WHEN** the same statements run against the database and `inspect_grants()` reads the result
- **THEN** `canonicalize_grants()` returns the same rows

#### Scenario: Unqualified table

- **WHEN** a statement names a table without a schema
- **THEN** the row uses `current_schema()` of the connection

#### Scenario: ALL privileges

- **WHEN** a statement uses `ALL` or `ALL PRIVILEGES`
- **THEN** the rows hold each privilege in `aclexplode(acldefault(...))` for the object type on the connected server

#### Scenario: Session role keywords

- **WHEN** a grantee is `CURRENT_USER`, `CURRENT_ROLE`, or `SESSION_USER`
- **THEN** the row holds the role name that the connection reports for that keyword

#### Scenario: Duplicate grants

- **WHEN** two statements give the same privilege to the same role, and one uses `WITH GRANT OPTION`
- **THEN** the result holds one row with `grantable=True`
