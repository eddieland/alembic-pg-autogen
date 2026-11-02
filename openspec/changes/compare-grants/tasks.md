## 1. Catalog Inspection Layer

- [x] 1.1 Add the `GrantInfo` NamedTuple to `src/alembic_pg_autogen/inspect.py` with the fields
  `(schema, object_type, name, grantee, privilege, grantable)`
- [x] 1.2 Add `_GRANTS_QUERY`. It reads `relacl` and `nspacl` through `aclexplode()`, omits owner privileges, sequences,
  and extension members, and merges grantors with `bool_or()`.
- [x] 1.3 Add `inspect_grants(conn, schemas=None, *, initial=False)`. `initial=True` reads `pg_init_privs`.
- [x] 1.4 Add inspection tests to `tests/alembic_pg_autogen/test_grants.py` for tables, views, schemas, `PUBLIC`, the
  grant option, the owner, sequences, column privileges, extension members, two grantors, the schema filter, and initial
  privileges

## 2. Canonicalization Layer

- [x] 2.1 Add `canonicalize_grants(conn, ddl)` to `src/alembic_pg_autogen/canonicalize.py`. It parses with postgast and
  does not execute the statements.
- [x] 2.2 Read `current_schema()`, `current_user`, `session_user`, and the `ALL` privilege sets from `acldefault()` in
  one query
- [x] 2.3 Raise `ValueError` for `REVOKE`, other statements, `ON ALL ... IN SCHEMA`, other object types, column
  privileges, and privileges that the object type does not have
- [x] 2.4 Add tests for expansion, equality with the catalog after execution, the default schema, session roles,
  duplicates, quoted identifiers, and each rejected form

## 3. Diff Layer

- [x] 3.1 Add `GrantOp` and `DiffResult.grant_ops` to `src/alembic_pg_autogen/diff.py`. Add `CanonicalState.grants`.
- [x] 3.2 Change `_diff_items` to compare whole tuples, so `grantable` works as the payload
- [x] 3.3 Add diff tests for `CREATE`, `DROP`, `REPLACE`, and equal grants

## 4. Operations and Rendering

- [x] 4.1 Add `GrantPrivilegesOp`, `RevokePrivilegesOp`, and `AlterGrantOptionOp` to `src/alembic_pg_autogen/ops.py`.
  Check that each group has one object, grantee, and grant option.
- [x] 4.2 Add renderers to `src/alembic_pg_autogen/render.py`. Quote identifiers, render `public` as `PUBLIC`, and sort
  privileges in documentation order.
- [x] 4.3 Add tests for `reverse()`, `to_diff_tuple()`, the group check, and each rendered statement

## 5. Comparator

- [x] 5.1 Add `pg_grants` to `_DESIRED_STATE_KEYS` in `src/alembic_pg_autogen/compare.py`
- [x] 5.2 Add `_grant_states()`. It removes initial privileges from both sides and filters the desired grants to the
  compared schemas.
- [x] 5.3 Add `_grant_migration_ops()`. Insert revokes and grant option changes at the start of `upgrade_ops.ops`.
  Append grants at the end.
- [x] 5.4 Add autogenerate tests for a new grant, matching grants, a revoke, grant option changes, `IGNORED`, a new
  table, a dropped table, and a new view. Apply the new table migration up and down.
- [x] 5.5 Export the new names from `src/alembic_pg_autogen/__init__.py`, and mention `pg_grants` in the `IGNORED`
  docstring

## 6. Documentation

- [x] 6.1 Add a "Grants" section to `README.md` and to `docs/quickstart.rst`
- [x] 6.2 Add a `PGGrantTable` note to `docs/migrating.rst`
