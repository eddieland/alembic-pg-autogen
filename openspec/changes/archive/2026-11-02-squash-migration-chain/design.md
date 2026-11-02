## Context

A project with a long Alembic chain replays every revision to build a fresh database. Issue #46 asks for a squash that
replaces the start of the chain with one revision. The caller names the last replaced revision, `TARGET`. See
`proposal.md` for motivation.

The squash needs no part of the autogenerate pipeline. It uses Alembic's `ScriptDirectory` and `command.upgrade()`,
`pg_dump`, and `postgast.split()`.

Every decision below rests on prototype work against PostgreSQL 16.15, `pg_dump` 16.15, SQLAlchemy 2.0.46, psycopg
3.3.3, and Alembic 1.19.1 (2026-11-02). The prototype script is committed next to this document as `prototype.py`. It
builds a nine revision chain, squashes `r01` to `r07`, and proves these facts:

1. `pg_dump --schema-only` emits the extension as one `CREATE EXTENSION IF NOT EXISTS`. It emits none of the 88 objects
   that `citext` owns. It emits no seed rows. It emits an enum as one `CREATE TYPE`, without the
   `ALTER TYPE ... ADD VALUE` of the old chain. It omits a dropped column.
1. `pg_dump` emits a function before the table that the function body reads. The dump sets
   `check_function_bodies = false`, so the body runs in that order.
1. The dump includes the `alembic_version` table. A body with that table fails under Alembic with `DuplicateTable`.
   `--exclude-table=public.alembic_version` removes the table and its primary key.
1. `pg_dump` 16.15 wraps its output in `\restrict KEY` and `\unrestrict KEY` lines. These are psql meta-commands. The
   key is random, so two dumps of one database differ. Without those two lines, the dumps are identical.
1. `postgast.split()` rejects a dump with the `\restrict` line. Without that line, `postgast.split()` splits the dump
   into statements.
1. `op.execute(str)` and `sa.text()` read `'a :b'` in a dumped `CHECK` constraint as a bind parameter, and the statement
   fails. `sa.DDL(statement.replace("%", "%%"))` runs the statement unchanged, `%` and `\` included.
1. The dump preamble runs `SELECT pg_catalog.set_config('search_path', '', false)`. This setting stays for the rest of
   the session. Alembic's next write to `alembic_version` then fails with `UndefinedTable`.
1. The revision can save each setting into a custom setting before the body runs and restore it after. A fresh database
   then reaches head through the revisions after `TARGET`.
1. A database built by the old chain to `r07` and a database built by the squashed revision give identical dumps. At
   head, the two chains also give identical dumps.
1. The squashed revision keeps the ID `r07`. A database at `r07` upgrades to head without `stamp`. A database at `r09`
   needs no change. A database at `r03` fails with `Can't locate revision identified by 'r03'`.
1. A revision after `TARGET` whose `down_revision` is `r05` makes the new chain fail to load with `KeyError: 'r05'`. On
   the old chain, `RevisionMap.iterate_revisions()` finds that revision before any file changes.
1. A module-level name in a revision file is readable as `ScriptDirectory.get_revision(rev).module.<name>`.
1. A downgrade that runs `DROP SCHEMA public CASCADE` also drops `alembic_version`. Alembic's next statement then fails
   with `UndefinedTable`.
1. `alembic upgrade --sql` renders the body with one `%` and with `'a :b'` unchanged.
1. A 300 revision chain of `create_table`, `add_column`, `create_index`, and `alter_column` calls takes 1.29 s to build.
   Its squash takes 0.17 s. The two dumps are identical.

## Goals / Non-Goals

**Goals:**

- Replace `TARGET` and its ancestors with one revision that builds the same schema
- Keep every database at or after `TARGET` valid without a `stamp`
- Compare a stated list of properties of both chains before the squash changes a file
- Keep the new revision a plain Alembic revision file that runs without this package

**Non-Goals:**

- Seed data in the dump. A schema-only dump holds no rows. The caller passes rows with `extra_sql` (D14).
- Choice of `TARGET`
- A body of Alembic operations instead of SQL. That body needs the dependency ordering from #24.
- Databases older than `TARGET`
- A downgrade past `TARGET`
- Global objects that `pg_dump` leaves out: roles, tablespaces, and `ALTER DATABASE ... SET` settings

## Decisions

### D1: The squash lives in this package, in a module that `__init__.py` does not import

Issue #46 asks whether the squash belongs here or in a separate tool. It belongs here, for these reasons:

- The package already requires `postgast`, and `postgast.split()` is the statement splitter (fact 5).
- The test suite already starts a real PostgreSQL server. The squash tests need one.
- The users of this package build PostgreSQL schemas with Alembic. They are the users with long chains.
- Issue #24 came from this package's users, for the same problem.

`alembic_pg_autogen/__init__.py` does not import `alembic_pg_autogen.squash`. A project that uses only autogenerate
loads no squash code.

### D2: `squash()` takes an Alembic `Config` and a server URL

```python
from alembic.config import Config

from alembic_pg_autogen.squash import squash

result = squash(
    Config("alembic.ini"),
    "3f9a1c2b7d4e",
    server_url="postgresql+psycopg://postgres@localhost/postgres",
)
```

The command line takes the same arguments:

```text
python -m alembic_pg_autogen.squash --config alembic.ini --target 3f9a1c2b7d4e \
    --server-url postgresql+psycopg://postgres@localhost/postgres
```

`server_url` names a server, not a database. The squash connects to it and creates scratch databases from `template0`.
It drops them at the end, also on failure. Other keyword arguments are `version_table` (default `"alembic_version"`),
`version_table_schema` (default `"public"`), `pg_dump` (the binary, default `"pg_dump"`), `pg_dump_args` (extra
arguments), `allow_data_loss`, and `dry_run`.

`squash()` returns a `SquashResult` named tuple. It holds the new file path, the removed file paths, the statement
count, the server version, and the `pg_dump` version. It also holds the text of the new file as `source`, and the data
differences that `allow_data_loss=True` accepts as `warnings` (D9). A failed check raises `SquashError`. The command
line prints the message and exits with status 1.

### D3: The new revision takes `TARGET`'s ID

`revision = TARGET` and `down_revision = None`. The revisions after `TARGET` keep `down_revision = TARGET`. A database
at `TARGET` or later finds its revision in the new chain (fact 10). No database needs a `stamp`.

Before any other work, the squash reads the old chain through `ScriptDirectory.walk_revisions()`. The replaced set is
`TARGET` plus every revision that `TARGET` reaches through `down_revision` and `depends_on`. The squash collects this
set itself, because `depends_on` adds ancestors that a walk of `down_revision` alone misses. The squash raises
`SquashError` in these cases:

- A revision outside the replaced set names a replaced revision other than `TARGET` in `down_revision` or `depends_on`
  (fact 11).
- A replaced revision other than `TARGET` declares `branch_labels` in its file. Alembic copies a label to every revision
  of its branch, so the squash reads `branch_labels` from each revision module, not from `Script`. The labels that
  `TARGET` declares carry over to the new revision.
- `TARGET` is not a revision in the chain.
- A revision outside the replaced set imports the module of a replaced revision. The squash deletes that module, so the
  import would fail after the squash. The check reads the `import` statements of each file with `ast`.
- `<TARGET>_squashed.py` exists and is not a replaced file.

Branches and merges inside the replaced set need no check. The squash removes all of them.

### D4: The body comes from a scratch database that the old chain builds

The squash runs `command.upgrade(config, TARGET)` against an empty scratch database. It then runs:

```text
pg_dump --schema-only --exclude-table='"<version_table_schema>"."<version_table>"' <scratch database>
```

The quoted pattern keeps the case of the names. The squash never reads a deployed database. A deployed database can hold
changes that no revision made. Those changes must not enter the chain. The current models are also not a source. They
describe head, not `TARGET`.

The dump keeps owners. The review of #53 showed that `--no-owner` on both compared dumps hides an
`ALTER TABLE t OWNER TO app_owner` in the chain: the squash passed, and a fresh database got a different owner. D5
removes only `OWNER TO` the role that ran the squash, because that role owns new objects already. `OWNER TO` another
role stays in the body. Privileges stay in the body too. A `GRANT` in the old chain is part of the schema.

### D5: The squash cleans the dump

1. Remove the `\restrict KEY` line and the matching `\unrestrict KEY` line (facts 4 and 5). The first line counts only
   when it is the first line that is not blank and not a comment. The second counts only as the last such line. An older
   `pg_dump` writes no wrapper, and a function body can hold the same text.
1. Split the text with `postgast.split()`. A `PgQueryError` becomes `SquashError`.
1. Remove the `--` comment lines before each statement. `pg_dump` writes these comments as headers. Comment lines inside
   a statement stay.
1. Remove `SET client_encoding`. The text is a Python `str`, and the driver encodes it. The squash reads the dump as
   bytes with `PGCLIENTENCODING=UTF8`, so `\r\n` inside a statement stays.
1. Remove `SET` and `set_config()` of a setting that the server does not know. `pg_dump` 17 and newer set
   `transaction_timeout`, which PostgreSQL 16 and older reject.
1. Remove `COMMENT ON EXTENSION`. Only the owner of the extension can run it.
1. Remove `ALTER ... OWNER TO` the role that ran the squash (D4). When a string in a replaced revision runs `OWNER TO` a
   role by name, keep every `OWNER TO`. That revision did not rely on the default owner, and a migration as another role
   must keep the owner.
1. Remove `FOR ROLE <role that ran the squash>` from `ALTER DEFAULT PRIVILEGES`. The replaced revision set default
   privileges for the role that ran it. Without the clause, the statement does the same for the role that runs the new
   revision. With the clause, a non-superuser migration role fails with `permission denied`.
1. Rewrite `CREATE SCHEMA` of the version table schema as `CREATE SCHEMA IF NOT EXISTS`. `env.py` often creates that
   schema before Alembic creates the version table.

The order of the statements does not change. `pg_dump` already orders objects by dependency (fact 2).

A schema-only dump creates each materialized view `WITH NO DATA`. The squash puts `REFRESH MATERIALIZED VIEW` for each
populated materialized view into `POST_STATEMENTS`, which run after the settings are restored. A function with an
unqualified table name then finds its table. The order comes from the `_RETURN` rules of the views, so other rules
cannot form a cycle. The squash skips the order when no materialized view is populated, and it reports a cycle as
`SquashError`. An unpopulated materialized view that a populated one reads is refreshed first and emptied again with
`WITH NO DATA` at the end. The `OWNER TO` of each refreshed view moves after its refresh, because the old chain
populated the view as the role that created it.

### D6: Each statement runs as `sa.DDL` with `%` doubled

`op.execute(str)` wraps the text in `sa.text()`, which treats `:name` as a bind parameter (fact 6). `sa.DDL` has no bind
parameters. It applies Python `%` formatting to the text, so the revision doubles every `%` first. The result is correct
online and in `--sql` mode (fact 14).

### D7: The revision restores every setting that the body changes

The dump changes session settings: `search_path`, `check_function_bodies`, `default_table_access_method`, and others.
These settings stay for the session, so they reach the revisions after `TARGET` and Alembic's own statements (fact 7).
`SET LOCAL` does not help, because Alembic runs every revision in one transaction by default.

The squash collects the setting names from the `SET` statements and the `set_config()` call in the body. Before the
body, the revision saves each setting into `alembic_pg_autogen.squash_<name>`. After the body, it restores each setting
from there (fact 8). This also works in `--sql` mode, because it is SQL.

### D8: The revision file holds the statements as a tuple

```python
"""Squashed 1a2b3c4d5e6f..3f9a1c2b7d4e (212 revisions).

Revision ID: 3f9a1c2b7d4e
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "3f9a1c2b7d4e"
down_revision = None
branch_labels = None
depends_on = None
squashed_revisions = ("1a2b3c4d5e6f", ..., "3f9a1c2b7d4e")

SETTINGS = ("statement_timeout", "search_path", "check_function_bodies", ...)
STATEMENTS = (
    "SET statement_timeout = 0",
    "CREATE TABLE public.account (\n    id integer NOT NULL,\n    email public.citext\n)",
    ...,
)


def upgrade():
    for name in SETTINGS:
        op.execute(sa.DDL(f"SELECT set_config('alembic_pg_autogen.squash_{name}', current_setting('{name}'), false)"))
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement.replace("%", "%%")))
    for name in SETTINGS:
        op.execute(sa.DDL(f"SELECT set_config('{name}', current_setting('alembic_pg_autogen.squash_{name}'), false)"))


def downgrade():
    raise NotImplementedError("3f9a1c2b7d4e squashes 212 revisions and cannot be downgraded")
```

The file imports nothing from this package. A project can remove the package later and keep the revision.

The file name is `<TARGET>_squashed.py`, in the directory of the old `TARGET` file. The squash deletes the replaced
files only after both checks in D9 pass.

### D9: Comparisons gate the write

The squash writes the new revision into a temporary copy of the script directory and of each version location. The copy
holds `env.py`, the template, the new revision, and the revisions after `TARGET`. The squash maps each file through the
version location that holds it, so a subdirectory of a recursive version location stays in the copy. It then loads the
copy and checks that `TARGET` has `down_revision = None` and that no other replaced revision remains.

Equal dumps do not prove equivalence. The review of #53 found materialized view population, ownership, inherited column
order, and global objects that all passed a dump comparison while behavior differed. The squash therefore compares a
list of properties, at `TARGET` and at the heads:

- the `pg_dump --schema-only` text, with owners and without the `\restrict` pair
- the column order of each table, view, and materialized view, from `pg_attribute`
- `relispopulated` of each materialized view
- the rows of the version table of each chain. These rows are compared with each other, not with
  `ScriptDirectory.get_heads()`, which lists heads that a dependency already satisfies.
- roles, role memberships, tablespaces, and `pg_db_role_setting` on the server (D13)

At `TARGET` only, the squash also compares the row count of each table, and `last_value` and `is_called` of each
sequence. `pg_sequences.last_value` is NULL until the first `nextval()`, so the squash reads each sequence relation. A
difference raises `SquashError` unless the caller passes `allow_data_loss=True`. Then the differences become
`SquashResult.warnings`.

On a difference, the squash raises `SquashError` with a unified diff and changes no file. With `dry_run=True`, the
squash runs every check and changes no file.

After every check passes, the squash writes the new file to a temporary name in the target directory, calls `fsync`, and
renames it with `os.replace`. Only then does it delete the replaced files. A failed write therefore changes no file.

### D10: `squashed_revisions` lists every replaced ID

Issue #46 asks whether the new revision carries a marker. It does. The marker is a tuple of every replaced ID, in order
from base to `TARGET`. The issue proposed the range string `"<old base>..<TARGET>"`. A tuple supports more checks:

- A guard in `env.py` or in a test finds a database at a replaced revision. It explains that the database must reach
  `TARGET` with an older release first (facts 10 and 12). Alembic's own error names only the revision.
- A chain-order check sees `down_revision = None` and the marker together, and does not report a second base.
- A chain with merges has more than one old base. A range string cannot name them.

A chain of 500 revisions with 12 character IDs makes a tuple of about 8 KB.

### D11: `downgrade()` raises `NotImplementedError`

A downgrade that drops every object must also keep `alembic_version`, or Alembic fails (fact 13). Dropping each object
in reverse dump order is possible. It gives a destructive operation that no deployed database needs. A test that builds
a fresh database never needs a schema older than `TARGET`.

### D12: A preflight checks that `env.py` uses the scratch database

The squash sets `sqlalchemy.url` on the `Config`. The generic `env.py` template reads that option. The squash also puts
an open connection into `config.attributes["connection"]`. The Alembic cookbook recipe "Sharing a Connection across one
or more programmatic migration commands" reads that attribute. The connection comes from `engine.connect()` and has no
open transaction, so `autocommit_block()` and `transaction_per_migration` work.

The first version checked the version table after each upgrade. The review of #53 showed that this check runs after the
upgrade commits: an `env.py` that reads `DATABASE_URL` migrated the real database first, also in a dry run.

The squash now runs a preflight before the first upgrade of the chain. The preflight copies the script directory without
any revision and adds one sentinel revision. The sentinel raises unless `current_database()` is the name of a scratch
database for the preflight. In that database, it creates a marker table. The squash stops unless the marker exists
afterward. A raised error alone is not enough proof, because an `env.py` can catch the error and continue.

The preflight protects the revisions of the chain. It cannot stop code that `env.py` runs before `run_migrations()`,
such as a committed `CREATE SCHEMA`, in the database that `env.py` chooses. Alembic also creates an empty version table
there before it runs the sentinel. That table stays with an `AUTOCOMMIT` engine, or with an `env.py` that catches the
sentinel's error. The documentation states both cases.

### D13: Objects of the whole server stop the squash

`pg_dump` does not dump roles, role memberships, tablespaces, or database and system settings. A difference of the
server state cannot prove what a clean cluster needs. The second review of #53 showed two gaps: an idempotent
`CREATE ROLE` passes on a server that has the role, and a change that the squash cannot drop disappears from the next
snapshot.

The squash therefore scans the string literals of each replaced revision with `ast`, before it connects to the server.
It refuses the chain when a string contains `CREATE`, `ALTER`, or `DROP` of a role, user, group, or tablespace, a role
`GRANT` or `REVOKE` without `ON`, `ALTER DATABASE`, or `ALTER SYSTEM`. `CREATE USER MAPPING` does not count. Comments
and the module docstring do not count.

The state check stays as a second check. The squash records the server state before the run and compares it after each
upgrade of the old chain. A difference raises `SquashError`, also for a revision after `TARGET`. The squash never drops
a role or a tablespace, because a difference cannot tell which session made it. A concurrent `CREATE ROLE` would
otherwise disappear. The error lists what remains, and the caller removes it.

### D14: `extra_sql` carries seed rows

A revision after `TARGET` can read a row that a replaced revision inserted. Without that row, the new chain fails. A row
that the caller adds to the new file by hand disappears in a later squash. `squash()` therefore takes `extra_sql`, and
the command line takes `--extra-sql FILE`. The squash splits the SQL with `postgast.split()` and stores the statements
in `EXTRA_STATEMENTS`. The revision runs them after the settings are restored. The comparison runs them too, so the row
count and sequence checks see them.

A later squash reads `EXTRA_STATEMENTS` from each replaced revision module and puts them before the new `extra_sql`. The
rows therefore move forward with each squash.

## Risks / Trade-offs

**\[An `env.py` that ignores both ways\]** → `env.py` connects to the database that it chooses. Mitigation: the D12
preflight runs no revision of the chain there. Code that `env.py` runs outside the migrations, and an empty version
table, can still reach that database.

**\[`pg_dump` and server versions\]** → `pg_dump` refuses a server with a newer major version. The body also uses syntax
of the server that the squash ran against. For example, PostgreSQL 15 adds `NULLS NOT DISTINCT`. Mitigation: the
documentation tells users to run the squash against the oldest major version they support. `SquashResult` records both
versions.

**\[`pg_dump` output changes between minor versions\]** → `\restrict` arrived in a minor release. Mitigation: both dumps
in one comparison come from one `pg_dump` binary. D5 removes `\restrict` whether or not it is present.

**[Lost rows]** → A replaced revision that inserted reference rows loses them. Mitigation: D9 compares row counts and
sequences at `TARGET` and stops by default, and D14 carries rows forward. Row contents are not compared.

**[A shared server]** → Another session that changes roles during the run stops the squash with a false difference.
Mitigation: the documentation asks for a server that only the caller uses during the run.

**[A large body]** → The revision file grows with the schema, not with the chain. A schema of 500 tables gives a file of
several thousand lines. Mitigation: none. The file replaces hundreds of files.

**\[A database older than `TARGET`\]** → Alembic reports `Can't locate revision`. Mitigation: D10 lets a project explain
the error. Choice of `TARGET` stays with the caller.

## Migration Plan

Not applicable to existing users. For a project that squashes, the change is one commit that adds a file and removes
files. A revert of that commit restores the old chain. No database changes, because the ID of `TARGET` stays the same.

## Open Questions

1. **Command line entry point.** D2 uses `python -m alembic_pg_autogen.squash`. A console script such as
   `alembic-pg-squash` is shorter. It adds a name to every environment that installs the package.
1. **Branch labels.** D3 refuses a replaced revision with labels other than `TARGET`'s. The squash can instead copy all
   labels to the new revision. Two labels can then name one branch.
1. **Readable statements.** D8 renders each statement with `repr()`, so a multi-line statement shows `\n`. A raw
   triple-quoted string is easier to review. It cannot hold `'''` or a final backslash, so the renderer needs a
   fallback.
1. **Privileges.** D4 keeps `GRANT` statements. A test cluster without the granted roles then fails. That cluster also
   fails on the old chain. A `--no-privileges` default removes the failure and also removes real schema.
