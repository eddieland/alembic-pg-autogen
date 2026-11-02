# migration-squash

## Purpose

Replaces a chosen revision and its ancestors with one revision that builds the same PostgreSQL schema, so a fresh
database replays fewer revisions.

## Requirements

### Requirement: Squash entry point

The module `alembic_pg_autogen.squash` SHALL provide
`squash(config, target, *, server_url, version_table="alembic_version", version_table_schema="public", pg_dump="pg_dump", pg_dump_args=(), allow_data_loss=False, dry_run=False)`.
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
branch labels in its file. Labels that Alembic propagates along a branch SHALL NOT count.

#### Scenario: Merge before the target

- **WHEN** the replaced set contains two branches and their merge revision
- **THEN** the squash replaces all of them

#### Scenario: Side branch from a removed revision

- **WHEN** revision `x` has `down_revision = "r01"` and the target is `r02`
- **THEN** `SquashError` names `x` and `r01`

#### Scenario: Label on the target

- **WHEN** only the target declares the branch label `core`
- **THEN** the squash proceeds and the new revision declares `core`

### Requirement: Preflight

Before the first upgrade of the old chain, the squash SHALL run `env.py` against a scratch database with a copy of the
script directory that holds only a sentinel revision. The sentinel SHALL raise unless `current_database()` is that
scratch database. A failure SHALL raise `SquashError` that names the two ways `env.py` must support.

#### Scenario: env.py that reads its own URL

- **WHEN** `env.py` replaces `sqlalchemy.url` with a URL from an environment variable, and that database is at `r01`
- **THEN** `SquashError` reports a failed preflight, also with `dry_run=True`
- **AND** that database stays at `r01` with the same tables

### Requirement: New revision file

The new revision SHALL have `revision` equal to the target ID, `down_revision = None`, the branch labels that the target
declares, and a module-level `squashed_revisions` tuple of the replaced IDs from base to the target. Its `upgrade()`
SHALL save each session setting that the body changes with `current_setting(name, true)`, run each statement as
`op.execute(sa.DDL(statement.replace("%", "%%")))`, and restore each saved setting that the server knows. Its
`downgrade()` SHALL raise `NotImplementedError`. The file SHALL import nothing from this package.

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
know, `COMMENT ON EXTENSION`, and `ALTER ... OWNER TO` the role that ran the squash. It SHALL rewrite `CREATE SCHEMA` of
the version table schema as `CREATE SCHEMA IF NOT EXISTS`. It SHALL append `REFRESH MATERIALIZED VIEW` for each
populated materialized view, in dependency order.

#### Scenario: Comment inside a function body

- **WHEN** a dumped function body contains a line that starts with `--` or `\restrict`
- **THEN** the new revision keeps that line

#### Scenario: Owner of another role

- **WHEN** a replaced revision runs `ALTER TABLE t OWNER TO app_owner`
- **THEN** the new revision keeps that statement, and a fresh database gets the owner `app_owner`

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
count of each table and the last value of each sequence. A difference SHALL raise `SquashError`, except that
`allow_data_loss=True` SHALL turn a row count or sequence difference into an entry of `warnings`.

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

#### Scenario: Global objects

- **WHEN** a replaced revision or a revision after the target runs `CREATE ROLE`
- **THEN** `SquashError` names the role, also with `dry_run=True`
- **AND** the role does not exist after the squash

#### Scenario: Dependency-satisfied head

- **WHEN** a revision after the target depends on a leaf revision of another branch
- **THEN** the squash compares the two version tables and proceeds

#### Scenario: Dry run

- **WHEN** `dry_run=True`
- **THEN** every check runs, `source` holds the new file, and no file changes

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
repeatable `--pg-dump-arg`, `--allow-data-loss`, and `--dry-run`. Without `--config`, it SHALL read `alembic.ini` and
`pyproject.toml` in the current directory. It SHALL exit with status 1 and print the message when a check fails or a
connection fails.

#### Scenario: Project with pyproject.toml only

- **WHEN** the current directory holds `pyproject.toml` with `[tool.alembic]` and no `alembic.ini`
- **THEN** the command finds the script directory and exits with status 0

#### Scenario: Refused connection

- **WHEN** the server refuses the connection
- **THEN** the command prints `squash failed` and exits with status 1
