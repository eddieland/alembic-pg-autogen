## Context

Functions, triggers, and views use one pipeline. The comparator inspects the catalog, canonicalizes the declared DDL in
a savepoint, compares the two snapshots, and emits operations. Grants enter the same pipeline through a new key,
`pg_grants`, in `_compare_pg_objects`. Two properties of grants change the design:

- A grant names an object that often does not exist yet. A new model and its grant arrive in the same autogenerate run.
- A grant is not one object. `GRANT SELECT, INSERT ON a, b TO x, y` is eight facts, and PostgreSQL stores each one.

## Goals / Non-Goals

**Goals:**

- Compare table and schema privileges with the declared `GRANT` statements.
- Emit `GRANT`, `REVOKE`, and grant option changes that apply in a valid order with Alembic's table operations.
- Never revoke a privilege that the database had from the start.

**Non-Goals:**

- Default privileges, column privileges, sequences, functions, and other object types
- `GRANT ... ON ALL TABLES IN SCHEMA`
- Role membership and role creation
- Tracking grantors

## Decisions

### D1: One row per object, grantee, and privilege

`GrantInfo` holds `(schema, object_type, name, grantee, privilege, grantable)`. The identity is every field except the
last. The payload is `grantable`. This matches the convention of the other catalog types. `_diff_items` therefore
handles grants without a special case. It now compares whole tuples, not `.definition`. For the existing types, the two
comparisons are equal, because matched items have the same identity.

A schema grant stores the schema name in both `schema` and `name`. The schema filter then works on one field for both
object types.

**Alternative:** one row per `GRANT` statement. Two statements can overlap, and PostgreSQL merges them. A statement
level comparison gives false differences.

### D2: Parse the declared grants, do not execute them

The other canonicalizers run DDL in a savepoint and read the catalog back. That fails for grants, because the table of a
new model does not exist during autogenerate. `canonicalize_grants()` parses each statement with postgast instead. The
parse tree gives the object type, the objects, the privileges, the grantees, and the grant option.

The parser lowercases unquoted identifiers, as PostgreSQL does. The connection supplies four values that the parser
cannot know:

- `current_schema()` qualifies an unqualified table.
- `current_user` and `session_user` replace `CURRENT_USER`, `CURRENT_ROLE`, and `SESSION_USER`.
- `aclexplode(acldefault('r', ...))` and `aclexplode(acldefault('n', ...))` give the privileges of `ALL`. The set
  depends on the server version. PostgreSQL 17 adds `MAINTAIN`.

The same sets validate each named privilege. `GRANT USAGE ON TABLE t` raises `ValueError` during autogenerate, not
during the migration.

**Trade-off:** a grant to a role that does not exist passes autogenerate and fails in the migration.

### D3: Read the ACL columns, not `information_schema`

The issue suggests `information_schema.role_table_grants`. That view shows only the grants where the current user or an
enabled role is the grantor or the grantee. A migration user that is not a superuser would miss grants. The inspector
reads `pg_class.relacl` and `pg_namespace.nspacl` through `aclexplode()` instead. One query covers tables and schemas.

The query omits:

- the privileges of the owner, because PostgreSQL gives them implicitly,
- objects that an extension owns, as for functions and triggers,
- sequences, because `GRANT ... ON SEQUENCE` uses other privileges.

Grants from two grantors give one row. `grantable` is the `bool_or()` of the rows.

The grantee `PUBLIC` reads as the lowercase name `public`. PostgreSQL reserves that name, so no real role has it. A role
created as `"PUBLIC"` is possible and keeps its uppercase name.

### D4: Remove initial privileges from both sides

`initdb` gives `USAGE` on the `public` schema to `PUBLIC`. Before PostgreSQL 15, it also gives `CREATE`. A user who
declares `pg_grants` and never mentions `public` would get a `REVOKE USAGE ON SCHEMA public FROM PUBLIC`. That breaks
every role that relies on the default.

`pg_init_privs` records these initial privileges. `inspect_grants(initial=True)` reads them with the same query. The
comparator removes each identity in that set from the current and the desired grants. A user who revoked an initial
privilege by hand therefore does not get it back. A declared initial privilege does not give a permanent `GRANT`.

### D5: Three operations, grouped per statement

The diff gives one `GrantOp` per privilege. The comparator groups them by object, grantee, and `grantable`, so the
migration holds one statement per group:

| Diff action | Operation            | Upgrade SQL                                                    | Reverse                               |
| ----------- | -------------------- | -------------------------------------------------------------- | ------------------------------------- |
| `CREATE`    | `GrantPrivilegesOp`  | `GRANT ... TO ... [WITH GRANT OPTION]`                         | `RevokePrivilegesOp`                  |
| `DROP`      | `RevokePrivilegesOp` | `REVOKE ... FROM ...`                                          | `GrantPrivilegesOp` with the old flag |
| `REPLACE`   | `AlterGrantOptionOp` | `GRANT ... WITH GRANT OPTION` or `REVOKE GRANT OPTION FOR ...` | the opposite change                   |

A separate operation for the grant option keeps `reverse()` correct. A `GrantPrivilegesOp` that only added the option
would reverse to a full `REVOKE`, and the downgrade would remove the privilege.

The renderers quote identifiers with the dialect's identifier preparer, as `DROP VIEW` does. The grantee `public`
renders as the keyword `PUBLIC`.

### D6: Revokes first, grants last

The comparator runs after Alembic's table comparator, because both register for `"schema"` at the same priority and
Alembic registers first. When it runs, `upgrade_ops.ops` already holds `create_table` and `drop_table`. The comparator
inserts the revokes and the grant option changes at index 0. It appends the grants after the function, view, and trigger
operations. Then:

- a grant on a new table or view follows its `CREATE`,
- a revoke on a dropped table or view precedes its `DROP`,
- the reversed list of `downgrade()` keeps both rules.

## Risks / Trade-offs

- **A declared list owns the whole schema.** Each undeclared grant on a table, view, or schema in the inspected schemas
  is revoked. This is the rule for every managed type. `IGNORED` and an absent key keep the old behavior.
- **`REVOKE` without `CASCADE`.** A role that passed a privilege on with the grant option makes `REVOKE` fail with
  "dependent privileges exist". The migration stops, and the user decides. A silent `CASCADE` removes grants that the
  declaration did not mention.
- **Grantors.** `REVOKE` removes only the grants of the role that runs it. For a superuser, that role is the owner of
  the object. A grant from another grantor stays, and the next autogenerate reports it again.
- **Grant to the owner.** The inspector omits the privileges of the owner. A declared grant to the owner therefore gives
  a `GRANT` on every run.
