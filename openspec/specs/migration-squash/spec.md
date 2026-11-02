# migration-squash

## Purpose

Replaces a chosen revision and its ancestors with one revision that builds the same PostgreSQL schema, so a fresh
database replays fewer revisions.

## Requirements

### Requirement: Squash entry point

The module `alembic_pg_autogen.squash` SHALL provide
`squash(config, target, *, server_url, version_table="alembic_version", version_table_schema="public", pg_dump="pg_dump", pg_dump_args=(), extra_sql=None, allow_data_loss=False, allow_global_statements=False, dry_run=False)`.
It SHALL return a `SquashResult` NamedTuple with fields `revision_path`, `removed_paths`, `statement_count`,
`server_version`, `pg_dump_version`, `source`, and `warnings`. It SHALL raise `SquashError` when a check fails. The
package `__init__` SHALL NOT import the module.

#### Scenario: Linear chain

- **WHEN** a chain `r01` to `r09` is squashed with `target="r07"`
- **THEN** `r07_squashed.py` is written in the directory of the old `r07` file
- **AND** the files of `r01` to `r07` are deleted
- **AND** `removed_paths` lists the deleted files from base to `r07`

#### Scenario: Symbolic target

- **WHEN** `target` is a unique prefix or `head`
- **THEN** the squash resolves it through `ScriptDirectory.get_revision()`

#### Scenario: Unknown target

- **WHEN** `target` names no revision, or names `base`
- **THEN** `SquashError` is raised and no file changes

#### Scenario: Server not reachable

- **WHEN** the connection to `server_url` fails
- **THEN** `SquashError` is raised

### Requirement: Replaced set and chain checks

The replaced set SHALL be the target and every revision that the target reaches through `down_revision` and
`depends_on`. The squash SHALL raise `SquashError` before it connects to the server in these cases: a revision outside
the replaced set names a replaced revision other than the target, or a replaced revision other than the target declares
branch labels in its file, a revision outside the replaced set imports the module of a replaced revision, or
`<target>_squashed.py` exists and is not a replaced file. Labels that Alembic propagates along a branch SHALL NOT count.

#### Scenario: Merge before the target

- **WHEN** the replaced set contains two branches and their merge revision
- **THEN** the squash replaces all of them

#### Scenario: Side branch from a removed revision

- **WHEN** revision `x` has `down_revision = "r01"` and the target is `r02`
- **THEN** `SquashError` names `x` and `r01`

#### Scenario: Import of a replaced module

- **WHEN** revision `r03` runs `from r01 import helper` and the target is `r02`
- **THEN** `SquashError` names `r03.py` and `r01.helper`

#### Scenario: Name collision

- **WHEN** a later revision file is named `r01_squashed.py` and the target is `r01`
- **THEN** `SquashError` names that file before the squash connects to the server

#### Scenario: Label on the target

- **WHEN** only the target declares the branch label `core`
- **THEN** the squash proceeds and the new revision declares `core`

### Requirement: Preflight

Before the first upgrade of the old chain, the squash SHALL run `env.py` against a scratch database with a copy of the
script directory that holds only a sentinel revision. The sentinel SHALL raise unless `current_database()` is that
scratch database, and SHALL otherwise create a marker table there. In another database, it SHALL write a trip file at a
path fixed in its template before it raises. During the preflight, the squash SHALL record each database that a
SQLAlchemy engine connects to. The squash SHALL raise `SquashError` that names the two ways `env.py` must support when
the run fails, an engine connects to another database, the trip file exists, or the marker does not exist.

#### Scenario: env.py that reads its own URL

- **WHEN** `env.py` replaces `sqlalchemy.url` with a URL from an environment variable, and that database is at `r01`
- **THEN** `SquashError` reports a failed preflight, also with `dry_run=True`
- **AND** that database stays at `r01` with the same tables

#### Scenario: env.py that catches errors

- **WHEN** `env.py` connects to another database and catches the error of `run_migrations()`
- **THEN** `SquashError` names that database

#### Scenario: env.py that migrates two databases

- **WHEN** `env.py` loops over the scratch URL and another URL, catches each error, and the other database is at `r01`
- **THEN** `SquashError` names the other database, also with `dry_run=True`
- **AND** that database stays at `r01` with the same tables

### Requirement: New revision file

The new revision SHALL have `revision` equal to the target ID, `down_revision = None`, the branch labels that the target
declares, and a module-level `squashed_revisions` tuple of the replaced IDs from base to the target. Its `upgrade()`
SHALL save each session setting that the body changes with `current_setting(name, true)`, run each statement as
`op.execute(sa.DDL(statement.replace("%", "%%")))`, and restore each saved setting that the server knows. It SHALL then
run `EXTRA_STATEMENTS` and `POST_STATEMENTS` the same way. Its `downgrade()` SHALL raise `NotImplementedError`. The file
SHALL import nothing from this package.

#### Scenario: Colon and percent in a statement

- **WHEN** a dumped statement contains `'a :b'` and `'%\\%'`
- **THEN** the statement runs unchanged online and in `--sql` mode

#### Scenario: Database at the target

- **WHEN** a database that the old chain built to the target runs `alembic upgrade head` with the new chain
- **THEN** it applies only the revisions after the target, without `stamp`

#### Scenario: Search path after the body

- **WHEN** the body runs `set_config('search_path', '', false)`
- **THEN** the revisions after the target and Alembic's version write run with the earlier `search_path`

### Requirement: Body from a scratch database

The squash SHALL create scratch databases from `template0` on the server of `server_url` and drop them at the end, also
on failure. It SHALL build the body from `pg_dump --schema-only` of a scratch database that the old chain built to the
target, with the version table excluded by a quoted pattern. It SHALL remove the `\restrict KEY` line and the matching
`\unrestrict KEY` line and no other line, split the text with `postgast.split()`, and remove the comment lines before
each statement. It SHALL remove `SET client_encoding`, `SET` and `set_config()` of a setting that the server does not
know, and `COMMENT ON EXTENSION`. It SHALL remove `ALTER ... OWNER TO` the role that ran the squash, except for an
object that a string in a replaced revision gives an owner by name. It SHALL never keep that statement for schema
`public`. It SHALL remove `FOR ROLE <role that ran the squash>` from `ALTER DEFAULT PRIVILEGES`. It SHALL rewrite
`CREATE SCHEMA` of the version table schema as `CREATE SCHEMA IF NOT EXISTS`. The `\restrict` line SHALL count only as
the first line that is not blank and not a comment.

`POST_STATEMENTS` SHALL refresh each populated materialized view in the order of the `_RETURN` rules. An unpopulated
materialized view that a populated one reads SHALL be refreshed first and emptied with `WITH NO DATA` at the end. The
`OWNER TO` of a refreshed view SHALL move after its refresh. A cycle SHALL raise `SquashError`.

#### Scenario: Comment inside a function body

- **WHEN** a dumped function body contains a line that starts with `--` or `\restrict`, with or without a wrapper
- **THEN** the new revision keeps that line

#### Scenario: Owner of another role

- **WHEN** a replaced revision runs `ALTER TABLE t OWNER TO app_owner`
- **THEN** the new revision keeps that statement, and a fresh database gets the owner `app_owner`

#### Scenario: Default privileges

- **WHEN** the dump holds `ALTER DEFAULT PRIVILEGES FOR ROLE app IN SCHEMA public GRANT ...` and the squash runs as
  `app`
- **THEN** the body holds `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ...`

#### Scenario: Materialized view refresh

- **WHEN** a populated view reads an unpopulated one, a view reads an unqualified function, and a view belongs to a role
  without table access
- **THEN** a fresh database built from the new chain has the same populated views as the old chain

#### Scenario: Rule cycle

- **WHEN** a rule on `v1` inserts into `v2`, and `v2` selects from `v1`
- **THEN** the squash proceeds

#### Scenario: Newer pg_dump

- **WHEN** `pg_dump` writes `SET transaction_timeout = 0` and the server does not know `transaction_timeout`
- **THEN** the new revision does not contain that setting

#### Scenario: Version table in its own schema

- **WHEN** `version_table="AlembicVer"` and `version_table_schema="meta"`
- **THEN** the body holds `CREATE SCHEMA IF NOT EXISTS meta` and no `AlembicVer` table

#### Scenario: pg_dump failure

- **WHEN** `pg_dump` exits with a non-zero status, or the binary does not exist
- **THEN** `SquashError` is raised, no file changes, and no scratch database remains

#### Scenario: postgast cannot parse the dump

- **WHEN** `postgast.split()` raises `PgQueryError`
- **THEN** `SquashError` is raised

### Requirement: Comparisons

Before it changes a file, the squash SHALL compare the two chains at the target and at the heads. It SHALL compare the
`pg_dump --schema-only` text with owners, the column order of each relation, `relispopulated` of each materialized view,
and the rows of the two version tables. After each upgrade of the old chain, it SHALL compare roles, role memberships,
tablespaces, and `pg_db_role_setting` with their state before the run. At the target, it SHALL also compare the row
count of each table, and `last_value` and `is_called` of each sequence relation. A difference SHALL raise `SquashError`,
except that `allow_data_loss=True` SHALL turn a row count or sequence difference into an entry of `warnings`. The squash
SHALL NOT drop a role or a tablespace. The error SHALL list the server changes that remain.

#### Scenario: Different schema at the heads

- **WHEN** a revision after the target creates an index only when seed rows exist
- **THEN** `SquashError` reports a difference at the heads and names the index
- **AND** no file changes

#### Scenario: Inherited column order

- **WHEN** a child table inherits from a parent that gets a column after the child exists
- **THEN** `SquashError` reports a difference in catalog properties and names the child table

#### Scenario: Lost rows

- **WHEN** a replaced revision inserts a row and `allow_data_loss` is false
- **THEN** `SquashError` names the table and the sequence

#### Scenario: Sequence restart

- **WHEN** a replaced revision runs `ALTER SEQUENCE s RESTART WITH 1000`
- **THEN** `SquashError` names the sequence with `last_value 1000 is_called False`

#### Scenario: Global objects in dynamic SQL

- **WHEN** a replaced revision or a revision after the target creates a role through dynamic SQL
- **THEN** `SquashError` lists the role as a change that remains on the server
- **AND** the role still exists after the squash

#### Scenario: Dependency-satisfied head

- **WHEN** a revision after the target depends on a leaf revision of another branch
- **THEN** the squash compares the two version tables and proceeds

#### Scenario: Dry run

- **WHEN** `dry_run=True`
- **THEN** every check runs, `source` holds the new file, and no file changes

### Requirement: Statement scan of the replaced revisions

Before it connects to the server, the squash SHALL scan the string literals of each replaced revision, without comments,
docstrings, and the values of `comment` and `doc` keyword arguments. It SHALL raise `SquashError` with the file and line
of each match for `CREATE`, `ALTER`, or `DROP` of a named role, user, group, or tablespace, a role `GRANT` or `REVOKE`
without `ON`, `ALTER DATABASE`, and `ALTER SYSTEM`. A match SHALL start a statement: at the start of the string, or
after `;`, `$$`, `BEGIN`, `THEN`, `ELSE`, `LOOP`, or `EXECUTE '`. `CREATE USER MAPPING` and object privileges SHALL NOT
match. With `allow_global_statements=True`, each match SHALL become an entry of `warnings`.

#### Scenario: CREATE ROLE in a replaced revision

- **WHEN** a replaced revision runs `CREATE ROLE reader`, also inside a `DO` block after `THEN`
- **THEN** `SquashError` names the file and line, and the squash creates no scratch database

#### Scenario: Text that is not a statement

- **WHEN** a replaced revision holds `COMMENT ON TABLE t IS 'Grant tokens to users'`, a docstring, a column comment, the
  enum labels `'create user'` and `'alter role'`, or `RAISE EXCEPTION 'cannot grant admin to self'`
- **THEN** the scan does not match

### Requirement: Extra SQL

`squash()` SHALL take `extra_sql`, and the command line SHALL take `--extra-sql FILE`. The squash SHALL split the SQL
with `postgast.split()` into `EXTRA_STATEMENTS`. The comparison SHALL run them with the new chain, and SHALL compare a
digest of the rows of each table that they touch. A later squash SHALL put the `EXTRA_STATEMENTS` of each replaced
revision module before the new `extra_sql`, and SHALL list them in `warnings`.

#### Scenario: Seed rows that a later revision reads

- **WHEN** a replaced revision inserts rows that a later revision reads, and `extra_sql` inserts the same rows
- **THEN** the squash proceeds, and a later squash that replaces the new revision keeps the rows

#### Scenario: A later revision changes a carried row

- **WHEN** `extra_sql` inserts `(1, 'old')`, a later revision updates it to `'new'`, and a squash replaces both
- **THEN** `SquashError` names the rows of the table, and with `allow_data_loss=True` `warnings` lists the carried
  statements

### Requirement: Write

After every check passes and unless `dry_run` is set, the squash SHALL write the new file to a temporary name in the
target directory, call `fsync`, rename it with `os.replace`, and only then delete the replaced files. A failed write
SHALL raise `SquashError` and change no file. A failed delete SHALL raise `SquashError` that names each remaining file.

#### Scenario: Disk full

- **WHEN** `os.replace` fails
- **THEN** `SquashError` states that no file changed, and every replaced file remains

### Requirement: Script directory copy

The squash SHALL copy the script directory and each version location outside it, map each revision file through the
version location that holds it, and check that the copy loads the target with `down_revision = None` and no other
replaced revision.

#### Scenario: Recursive version location

- **WHEN** `recursive_version_locations = true` and the revision files are in a subdirectory of the version location
- **THEN** the squash writes the new revision into that subdirectory and compares the right chains

### Requirement: Command line

`python -m alembic_pg_autogen.squash` SHALL accept `--config` (repeatable, an ini file or `pyproject.toml`, as for the
`alembic` command), `--name`, `--target`, `--server-url`, `--version-table`, `--version-table-schema`, `--pg-dump`, a
repeatable `--pg-dump-arg`, `--extra-sql`, `--allow-data-loss`, `--allow-global-statements`, and `--dry-run`. Without
`--config`, it SHALL read `alembic.ini` and `pyproject.toml` in the current directory. It SHALL exit with status 1 and
print the message when a check fails or a connection fails.

#### Scenario: Project with pyproject.toml only

- **WHEN** the current directory holds `pyproject.toml` with `[tool.alembic]` and no `alembic.ini`
- **THEN** the command finds the script directory and exits with status 0

#### Scenario: Refused connection

- **WHEN** the server refuses the connection
- **THEN** the command prints `squash failed` and exits with status 1
