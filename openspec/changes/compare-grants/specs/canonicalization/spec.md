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

#### Scenario: Unqualified existing table

- **WHEN** the `search_path` is `a, public`, the table `public.orders` exists, and a statement names `orders`
- **THEN** the row uses the schema `public`, which `to_regclass()` finds, as `GRANT` does

#### Scenario: Unqualified new table

- **WHEN** a statement names a table without a schema and no relation has that name
- **THEN** the row uses `current_schema()` of the connection

#### Scenario: Relation that is not a table

- **WHEN** a statement in the form `GRANT SELECT ON s` names an existing sequence, index, or composite type
- **THEN** `canonicalize_grants()` raises `ValueError` that names the kind of relation

#### Scenario: GRANTED BY and PUBLIC with grant option

- **WHEN** a statement has `GRANTED BY`, or gives `WITH GRANT OPTION` to `PUBLIC`
- **THEN** `canonicalize_grants()` raises `ValueError`

#### Scenario: ALL privileges

- **WHEN** a statement uses `ALL` or `ALL PRIVILEGES`
- **THEN** the rows hold each privilege in `aclexplode(acldefault(...))` for the object type on the connected server

#### Scenario: Session role keywords

- **WHEN** a grantee is `CURRENT_USER`, `CURRENT_ROLE`, or `SESSION_USER`
- **THEN** the row holds the role name that the connection reports for that keyword

#### Scenario: Duplicate grants

- **WHEN** two statements give the same privilege to the same role, and one uses `WITH GRANT OPTION`
- **THEN** the result holds one row with `grantable=True`

### Requirement: Privileges of ALL

The module SHALL provide `all_grant_privileges(conn)`. It SHALL return the privileges of `ALL` on the connected server,
for `"TABLE"` and for `"SCHEMA"`, from `acldefault()`.

#### Scenario: Schema privileges

- **WHEN** `all_grant_privileges(conn)` runs
- **THEN** the `"SCHEMA"` entry is `{"USAGE", "CREATE"}`

### Requirement: Targets declared with ALL

The module SHALL provide `parse_grants(conn, ddl)`. It SHALL return a `DeclaredGrants` with the grants of
`canonicalize_grants()` and the `(schema, object_type, name, grantee)` targets of `GRANT ALL [PRIVILEGES]` statements.

#### Scenario: ALL and a list

- **WHEN** `ddl` holds `GRANT ALL ON a TO x` and `GRANT SELECT, INSERT ON b TO x`
- **THEN** `all_privileges` holds the target of `a` for `x` and not the target of `b`
