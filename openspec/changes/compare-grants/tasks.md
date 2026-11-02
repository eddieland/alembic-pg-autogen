## 1. Catalog Inspection Layer

- [x] 1.1 Add the `GrantInfo` NamedTuple to `src/alembic_pg_autogen/inspect.py` with the fields
  `(schema, object_type, name, grantee, privilege, grantable)`
- [x] 1.2 Add `_GRANTS_QUERY`. It reads `relacl` and `nspacl` through `aclexplode()`, omits owner privileges and
  sequences, and merges grantors with `bool_or()`.
- [x] 1.3 Add `inspect_grants(conn, schemas=None, *, source="current")`. `"initial"` reads `pg_init_privs`, and
  `"default"` reads `pg_default_acl`.
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

- [x] 3.1 Add `GrantOp` and `diff_grants()` to `src/alembic_pg_autogen/diff.py`. Keep `CanonicalState` and `DiffResult`
  unchanged.
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

## 7. Review Fixes

- [x] 7.1 Leave default privileges and `PUBLIC` on the `public` schema unmanaged in `_grant_states()`
- [x] 7.2 Warn for each declared object outside the compared schemas, with a hint about `include_schemas`
- [x] 7.3 Apply `include_name` and `include_object`, and skip the version table, in `_included_objects()`
- [x] 7.4 Include extension members in `inspect_grants()`, so declared grants on them converge
- [x] 7.5 Render `ALL` for a group with every privilege of the server
- [x] 7.6 Resolve table names with `to_regclass()` and reject sequences named without `SEQUENCE`
- [x] 7.7 Quote identifiers with a preparer that does not double `%` in `src/alembic_pg_autogen/render.py`
- [x] 7.8 Make revokes on dropped tables and views implicit, and keep the grants in `downgrade()`
- [x] 7.9 Reject `GRANTED BY` and `WITH GRANT OPTION` for `PUBLIC`
- [x] 7.10 Add a test for each item above in `tests/alembic_pg_autogen/test_grants.py`

## 8. Second Review

- [x] 8.1 Look up metadata tables with `is None` checks, because `Table` defines no truth value
- [x] 8.2 Remove initial and default privileges from the current grants only, and only when undeclared
- [x] 8.3 Pass `None` for the default schema to `include_name`, and warn when a filter removes a declared grant
- [x] 8.4 Render `ALL` only for targets that the declaration grants with `ALL`, through `parse_grants()`
- [x] 8.5 Leave only `USAGE`, and `CREATE` before PostgreSQL 15, of `PUBLIC` on `public` unmanaged
- [x] 8.6 Render the implicit revoke comment through `repr()`
- [x] 8.7 Warn when `pg_grants` declares a grant on a table or view that the migration drops
- [x] 8.8 Document that user-added grants on extension objects are managed
- [x] 8.9 Add a test for each item above
