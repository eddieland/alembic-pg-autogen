# Spec Delta

## Purpose

Replaces a chosen revision and its ancestors with one revision that builds the same PostgreSQL schema, so a fresh
database replays fewer revisions.

## ADDED Requirements

### Requirement: Squash entry point

The module `alembic_pg_autogen.squash` SHALL provide
`squash(config, target, *, server_url, version_table="alembic_version", version_table_schema="public", pg_dump="pg_dump", pg_dump_args=(), dry_run=False)`.
It SHALL return a `SquashResult` NamedTuple with fields `revision_path`, `removed_paths`, `statement_count`,
`server_version`, `pg_dump_version`, `source`, and `warnings`. It SHALL raise `SquashError` when a check fails. The
package `__init__` SHALL NOT import the module.

#### Scenario: Linear chain

- **WHEN** a chain `r01` to `r09` is squashed with `target="r07"`
- **THEN** the files of `r01` to `r07` are deleted
- **AND** `r07_squashed.py` is written in the directory of the old `r07` file
- **AND** `removed_paths` lists the deleted files from base to `r07`

#### Scenario: Symbolic target

- **WHEN** `target` is a unique prefix or `head`
- **THEN** the squash resolves it through `ScriptDirectory.get_revision()`

#### Scenario: Unknown target

- **WHEN** `target` names no revision, or names `base`
- **THEN** `SquashError` is raised and no file changes

### Requirement: Replaced set and chain checks

The replaced set SHALL be the target and every revision that the target reaches through `down_revision` and
`depends_on`. The squash SHALL raise `SquashError` before it connects to the server in these cases: a revision outside
the replaced set names a replaced revision other than the target, or a replaced revision other than the target has
branch labels.

#### Scenario: Merge before the target

- **WHEN** the replaced set contains two branches and their merge revision
- **THEN** the squash replaces all of them

#### Scenario: Side branch from a removed revision

- **WHEN** revision `x` has `down_revision = "r01"` and the target is `r02`
- **THEN** `SquashError` names `x` and `r01`

#### Scenario: Dependency on the target

- **WHEN** a revision outside the replaced set has `depends_on` equal to the target
- **THEN** the squash proceeds

### Requirement: New revision file

The new revision SHALL have `revision` equal to the target ID, `down_revision = None`, the branch labels of the target,
and a module-level `squashed_revisions` tuple of the replaced IDs from base to the target. Its `upgrade()` SHALL save
each session setting that the body changes, run each statement as `op.execute(sa.DDL(statement.replace("%", "%%")))`,
and restore each saved setting. Its `downgrade()` SHALL raise `NotImplementedError`. The file SHALL import nothing from
this package.

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
on failure. It SHALL build the body from
`pg_dump --schema-only --no-owner --exclude-table=<version_table_schema>.<version_table>` of a scratch database that the
old chain built to the target. It SHALL remove the `\restrict` and `\unrestrict` lines, split the text with
`postgast.split()`, remove the comment lines before each statement, and remove `SET client_encoding`. It SHALL keep
comment lines inside a statement.

#### Scenario: Comment inside a function body

- **WHEN** a dumped function body contains a line that starts with `--`
- **THEN** the new revision keeps that line

#### Scenario: pg_dump failure

- **WHEN** `pg_dump` exits with a non-zero status, or the binary does not exist
- **THEN** `SquashError` is raised, no file changes, and no scratch database remains

### Requirement: Equivalence checks

Before it changes a file, the squash SHALL compare `pg_dump --schema-only --no-owner` output of the two chains at the
target and again at the heads, without the `\restrict` lines. A difference SHALL raise `SquashError` with a unified
diff. After each upgrade, the squash SHALL check that the scratch version table holds the expected revisions.

#### Scenario: Different schema at the heads

- **WHEN** a revision after the target creates an index only when seed rows exist
- **THEN** `SquashError` reports a difference at the heads and names the index
- **AND** no file changes

#### Scenario: env.py ignores the scratch database

- **WHEN** `env.py` reads its URL from an environment variable
- **THEN** `SquashError` states that `env.py` must read `sqlalchemy.url` or use `config.attributes["connection"]`

#### Scenario: Dry run

- **WHEN** `dry_run=True`
- **THEN** both checks run, `source` holds the new file, and no file changes

### Requirement: Warnings for effects outside the dump

The squash SHALL report each replaced file that holds a data statement (`bulk_insert`, `INSERT INTO`, `UPDATE ... SET`,
or `DELETE FROM`) or a global object (`CREATE` or `ALTER` of a role, a user, or a tablespace, or `ALTER DATABASE`). It
SHALL log each message and return it in `warnings`.

#### Scenario: Seed rows

- **WHEN** a replaced revision runs `INSERT INTO account ...`
- **THEN** `warnings` names that file

### Requirement: Command line

`python -m alembic_pg_autogen.squash` SHALL accept `--config`, `--name`, `--target`, `--server-url`, `--version-table`,
`--version-table-schema`, `--pg-dump`, a repeatable `--pg-dump-arg`, and `--dry-run`. It SHALL exit with status 1 and
print the message when a check fails.

#### Scenario: Dry run from the command line

- **WHEN** the command runs with `--dry-run`
- **THEN** it prints the new revision and exits with status 0
