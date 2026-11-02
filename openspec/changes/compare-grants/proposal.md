## Why

Alembic does not compare privileges. This package did not compare them either. A `GRANT` in a migration is therefore
invisible to `alembic check`. A rebuilt or squashed schema loses it, and nothing reports the loss. `alembic_utils` has
`PGGrantTable`, but a project that moves from `alembic_utils` to this package loses that feature. Issue #48 asks for it.

## What Changes

- Add a `pg_grants` desired-state key, like `pg_functions`. It holds `GRANT` statements as SQL strings. `IGNORED` and an
  absent key leave grants unmanaged, as for the other keys.
- Add `GrantInfo` and `inspect_grants()` to the catalog inspector. The query reads `pg_class.relacl` and
  `pg_namespace.nspacl` through `aclexplode()`. It returns one row per object, grantee, and privilege.
- Add `canonicalize_grants()`. It parses each `GRANT` with postgast and expands it to the same rows. It does not execute
  the statements, because a grant can name a table that the same migration creates.
- Add `GrantOp` and `diff_grants()` to the diff layer. The identity of a grant is
  `(schema, object_type, name, grantee, privilege)`. The payload is `grantable`. `CanonicalState` and `DiffResult` do
  not change.
- Add `GrantPrivilegesOp`, `RevokePrivilegesOp`, and `AlterGrantOptionOp`, with renderers that emit `op.execute()`.
- Put revokes before every other operation and grants after every other operation. A revoke on an object that the
  migration drops is implicit.
- Leave initial privileges, default privileges, `PUBLIC` on the `public` schema, the version table, and objects that the
  Alembic filters exclude unmanaged.

## Non-goals

- **Default privileges** (`ALTER DEFAULT PRIVILEGES`). The issue excludes them until a user needs them.
- **Column privileges.** They are in `pg_attribute.attacl`. `canonicalize_grants()` rejects them.
- **Other object types.** Sequences, functions, types, and databases have other privilege names.
- **`GRANT ... ON ALL TABLES IN SCHEMA`.** The statement expands against the tables that exist when it runs. A desired
  state cannot express that.
- **Role membership** (`GRANT role TO role`) and role creation.
- **Grantors.** Two grantors of one privilege give one row. `REVOKE` removes only the grants of the migration user.

## Capabilities

### New Capabilities

- `grant-comparison`: compares declared table and schema grants with the catalog, and emits `GRANT` and `REVOKE`

### Modified Capabilities

- `catalog-inspector`: add `GrantInfo` and `inspect_grants()`
- `canonicalization`: add `canonicalize_grants()`
- `diff`: add `GrantOp` and `DiffResult.grant_ops`
- `alembic-compare`: add the `pg_grants` key and the position of grant operations

## Impact

- **Config**: the new key `pg_grants`. Configurations without the key behave as before.
- **Behavior**: a configuration that declares `pg_grants` revokes undeclared grants in the inspected schemas. Initial
  privileges in `pg_init_privs` and the privileges of the owner stay.
- **Backwards compatibility**: `CanonicalState` and `DiffResult` do not change, so unpacking into three names works.
- **Dependencies**: none new. postgast already parses `GRANT`.
- **Public API**: the new exports are `GrantInfo`, `inspect_grants`, `canonicalize_grants`, `GrantOp`, `diff_grants`,
  `GrantPrivilegesOp`, `RevokePrivilegesOp`, and `AlterGrantOptionOp`.
