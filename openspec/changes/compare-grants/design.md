## Context

Functions, triggers, and views use one pipeline. The comparator inspects the catalog, canonicalizes the declared DDL in
a savepoint, compares the two snapshots, and emits operations. Grants enter the same comparator through a new key,
`pg_grants`, in `_compare_pg_objects`. Three properties of grants change the design:

- A grant names an object that often does not exist yet. A new model and its grant arrive in the same autogenerate run.
- A grant is not one object. `GRANT SELECT, INSERT ON a, b TO x, y` is eight facts, and PostgreSQL stores each one.
- PostgreSQL adds privileges without a `GRANT` statement. `initdb`, `CREATE EXTENSION`, and `ALTER DEFAULT PRIVILEGES`
  all write to the same ACL columns that the inspector reads.

## Goals / Non-Goals

**Goals:**

- Compare table and schema privileges with the declared `GRANT` statements.
- Emit `GRANT`, `REVOKE`, and grant option changes that apply in a valid order with Alembic's table operations.
- Never revoke a privilege that PostgreSQL added without a `GRANT` statement.
- Respect the Alembic filters and the version table, as Alembic's table comparator does.

**Non-Goals:**

- Managing default privileges, column privileges, sequences, functions, and other object types
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

Grants have their own function, `diff_grants()`. `CanonicalState` and `DiffResult` keep their three fields. A fourth
field would break code that unpacks them into three names.

**Alternative:** one row per `GRANT` statement. Two statements can overlap, and PostgreSQL merges them. A statement
level comparison gives false differences.

### D2: Parse the declared grants, do not execute them

The other canonicalizers run DDL in a savepoint and read the catalog back. That fails for grants, because the table of a
new model does not exist during autogenerate. `canonicalize_grants()` parses each statement with postgast instead. The
parse tree gives the object type, the objects, the privileges, the grantees, and the grant option.

The parser lowercases unquoted identifiers, as PostgreSQL does. The connection supplies the values that the parser
cannot know:

- `to_regclass()` finds an existing relation through the `search_path`, as `GRANT` does. A name in a later `search_path`
  schema therefore gets that schema. A name that finds no relation gets `current_schema()`, because the migration
  creates the table in the first schema of the `search_path`.
- The same lookup gives the `relkind`. `GRANT SELECT ON seq` is valid SQL for a sequence, but the inspector does not
  read sequences. The parser rejects it, so it cannot give a `GRANT` on every run.
- `current_user` and `session_user` replace `CURRENT_USER`, `CURRENT_ROLE`, and `SESSION_USER`.
- `aclexplode(acldefault('r', ...))` and `aclexplode(acldefault('n', ...))` give the privileges of `ALL`. The set
  depends on the server version. PostgreSQL 17 adds `MAINTAIN`.

The same sets validate each named privilege. `GRANT USAGE ON TABLE t` raises `ValueError` during autogenerate, not
during the migration. The parser also rejects `GRANTED BY`, because the comparison ignores grantors, and
`WITH GRANT OPTION` for `PUBLIC`, which PostgreSQL refuses.

**Trade-off:** a grant to a role that does not exist passes autogenerate and fails in the migration.

### D3: Read the ACL columns, not `information_schema`

The issue suggests `information_schema.role_table_grants`. That view shows only the grants where the current user or an
enabled role is the grantor or the grantee. A migration user that is not a superuser would miss grants. The inspector
reads `pg_class.relacl` and `pg_namespace.nspacl` through `aclexplode()` instead. One query covers tables and schemas.

The query omits:

- the privileges of the owner, because PostgreSQL gives them implicitly,
- sequences, because `GRANT ... ON SEQUENCE` uses other privileges.

The query includes objects that an extension owns. A declared grant on an extension object must be visible, or it gives
a `GRANT` on every run. The privileges that `CREATE EXTENSION` gives are in `pg_init_privs`, so D4 removes them.

Grants from two grantors give one row. `grantable` is the `bool_or()` of the rows.

The grantee `PUBLIC` reads as the lowercase name `public`. PostgreSQL reserves that name, so no real role has it. A role
created as `"PUBLIC"` is possible and keeps its uppercase name.

### D4: Leave privileges that PostgreSQL added unmanaged unless declared

A user who declares `pg_grants` should not lose privileges that no `GRANT` statement created. The comparator removes
these identities from the current grants when `pg_grants` does not declare them:

- **Initial privileges.** `pg_init_privs` records the privileges of `initdb` and `CREATE EXTENSION`.
  `inspect_grants(source="initial")` reads them.
- **Default privileges.** `pg_default_acl` holds the privileges of `ALTER DEFAULT PRIVILEGES` for each owner.
  `inspect_grants(source="default")` reads the entries for the owner of each object, for all schemas and for the schema
  of the table. Without this rule, each new table gives a `REVOKE`, and a second autogenerate is not empty.
- **The `initdb` defaults of `PUBLIC` on the `public` schema.** These are `USAGE`, and `CREATE` before PostgreSQL 15.
  `DROP SCHEMA public` removes the `pg_init_privs` row. A script that creates the schema again often grants these
  privileges again, and the first rule no longer covers them. Other privileges of `PUBLIC` on `public` stay managed, so
  a `CREATE` that a user removes from `pg_grants` on PostgreSQL 15 or later is revoked.

The desired grants keep every declared privilege. The default ACL covers every table of the owner, including a table
created before the entry, and such a table never received the privilege. A rule on both sides would drop that declared
grant, and it would also hide a declared grant option. With the rule on the current side only, a declared privilege is
compared as usual. An undeclared one stays, and a user who revoked one by hand does not get it back.

### D5: Respect the Alembic filters and the version table

Alembic's table comparator skips the version table and applies `include_name` and `include_object`. The grant comparator
does the same for each table and view:

- `include_name` with type `"table"` and `schema_name=None` for the default schema, as Alembic passes it.
- `include_object` with the metadata `Table` and `reflected=False` when `target_metadata` has the table. Otherwise the
  object is a `Table` with a name and a schema but no columns, with `reflected=True`.

A schema goes through `include_name` with type `"schema"`. The default schema reaches the filter as `None`, as Alembic
passes it, so the cookbook filter `name in [None, ...]` keeps it. The metadata lookups test for `None`, because `Table`
defines no truth value.

A declared grant outside the compared schemas cannot be compared, because the inspector does not read that schema. The
comparator logs one warning for each such object, with the object name and a hint about `include_schemas`. A declared
grant that a filter or the version table rule removes also gives a warning.

### D6: Three operations, grouped per statement

The diff gives one `GrantOp` per privilege. The comparator groups them by object, grantee, and `grantable`, so the
migration holds one statement per group:

| Diff action | Operation            | Upgrade SQL                                                    | Reverse                               |
| ----------- | -------------------- | -------------------------------------------------------------- | ------------------------------------- |
| `CREATE`    | `GrantPrivilegesOp`  | `GRANT ... TO ... [WITH GRANT OPTION]`                         | `RevokePrivilegesOp`                  |
| `DROP`      | `RevokePrivilegesOp` | `REVOKE ... FROM ...`                                          | `GrantPrivilegesOp` with the old flag |
| `REPLACE`   | `AlterGrantOptionOp` | `GRANT ... WITH GRANT OPTION` or `REVOKE GRANT OPTION FOR ...` | the opposite change                   |

A separate operation for the grant option keeps `reverse()` correct. A `GrantPrivilegesOp` that only added the option
would reverse to a full `REVOKE`, and the downgrade would remove the privilege.

A group renders `ALL` when the declaration used `ALL` for its target and the group holds every privilege of `ALL` on the
autogenerate server. Thus a migration from PostgreSQL 17 does not send `MAINTAIN` to PostgreSQL 14. `ALL` means the set
of the server that runs the migration, so privileges that the declaration names one by one always render as a list.
Otherwise a list of seven privileges from PostgreSQL 14 would grant `MAINTAIN` on PostgreSQL 17. `parse_grants()`
reports the targets of `GRANT ALL` statements for this rule.

The renderers quote identifiers with a PostgreSQL preparer in the `named` paramstyle. The preparer of a `pyformat`
driver, such as psycopg, doubles `%`, and `op.execute()` does not undo that. The grantee `public` renders as the keyword
`PUBLIC`.

### D7: Revokes first, grants last, no revoke before a drop

The comparator runs after Alembic's table comparator, because both register for `"schema"` at the same priority and
Alembic registers first. When it runs, `upgrade_ops.ops` already holds `create_table` and `drop_table`. The comparator
inserts the revokes and the grant option changes at index 0. It appends the grants after the function, view, and trigger
operations. Then a grant on a new table or view follows its `CREATE`.

A `REVOKE` before the `DROP` of its object is not needed, because the `DROP` removes every privilege. It can also fail:
when one role passed the privilege on to another, `REVOKE` fails with "dependent privileges exist". For each table in a
`DropTableOp` and each view in a `DropViewOp`, the revoke is implicit. It renders as `pass` and a comment, and its
reverse still grants the privileges in `downgrade()`, after the object is created again. A grant option change on such
an object is omitted. The statement in the comment goes through `repr()`, because an identifier can hold a line break.

A grant that `pg_grants` still declares on a dropped object gives a warning. The next autogenerate would emit a `GRANT`
on a relation that no longer exists.

## Risks / Trade-offs

- **A declared list owns the compared schemas.** Each undeclared grant on a table, view, or schema in the inspected
  schemas is revoked, except for the privileges in D4 and the objects in D5. This is the rule for every managed type.
  `IGNORED` and an absent key keep the old behavior.
- **`include_schemas`.** Without `include_schemas=True`, autogenerate compares the default schema only. Grants in other
  schemas give a warning and no operation.
- **Extension objects.** A grant that a user added on an extension object is managed. With `include_schemas=True`, an
  undeclared one in an extension schema is revoked.
- **`REVOKE` without `CASCADE`.** On an object that the migration keeps, a role that passed a privilege on makes
  `REVOKE` fail with "dependent privileges exist". The migration stops, and the user decides. A silent `CASCADE` removes
  grants that the declaration did not mention.
- **Server upgrades.** A declared `GRANT ALL` means every privilege of the server. After an upgrade to a version with a
  new privilege, autogenerate emits a `GRANT` for that privilege.
- **Default privileges are approximate.** The rule uses the owner of the object, because PostgreSQL does not record
  which role created it. An undeclared privilege that matches a default ACL entry stays, even when a user granted it by
  hand.
- **Grantors.** `REVOKE` removes only the grants of the role that runs it. For a superuser, that role is the owner of
  the object. A grant from another grantor stays, and the next autogenerate reports it again.
- **Grant to the owner.** The inspector omits the privileges of the owner. A declared grant to the owner therefore gives
  a `GRANT` on every run.
