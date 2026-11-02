## Why

A test that needs a database at head replays every revision in the chain. With hundreds of revisions, this replay
controls the time to build a test database. In the prototype, a light 300 revision chain takes 1.29 s. One squashed
revision builds the same schema in 0.17 s. Real chains also contain data migrations, so their replay takes longer.

No maintained tool replaces an Alembic chain with one revision. The Alembic cookbook covers `create_all` plus `stamp`.
That recipe does not give a revision that later revisions can build on. Issue #24 drafted a two revision workflow with a
comparator skip flag and dependency ordering. That work stopped. This change uses a different route that needs neither.

## What Changes

- Add `alembic_pg_autogen.squash`, a module with a `squash()` function and a `python -m` command line
- The caller names a revision `TARGET`. The squash replaces every ancestor of `TARGET`, and `TARGET` itself, with one
  revision.
- The new revision takes the ID of `TARGET` and has `down_revision = None`. Revisions after `TARGET` stay unchanged. A
  database at or after `TARGET` needs no `stamp`.
- The body is the `pg_dump --schema-only` output of a scratch database that the old chain built up to `TARGET`. The
  squash never reads a deployed database.
- The new revision lists the replaced IDs in a module-level `squashed_revisions` tuple
- `downgrade()` raises `NotImplementedError`
- Before the squash changes a file, it compares `pg_dump` output in two places. The first place is `TARGET`: the old
  chain against the new revision. The second place is the heads: the old chain against the new revision plus the
  revisions after `TARGET`. Any difference stops the squash and prints a diff.
- The squash refuses a chain where a revision after `TARGET` names a replaced revision other than `TARGET`

## Non-goals

- **Seed data.** A schema-only dump holds no rows. The caller adds `op.bulk_insert()` for reference rows.
- **Choice of `TARGET`.** Only the caller knows the newest revision that every deployed database has reached.
- **Alembic operations in the body.** The body is SQL. Rendering it as `op.create_table()` calls needs the dependency
  ordering from #24.
- **Databases older than `TARGET`.** Alembic cannot find their revision after the squash. They upgrade to `TARGET` with
  an older release first.
- **A downgrade past `TARGET`.**
- **Servers without `pg_dump`.** The squash needs a `pg_dump` binary and a PostgreSQL role that can create databases.

## Capabilities

### New Capabilities

- `migration-squash`: replace the ancestors of a chosen revision with one revision that builds the same schema

### Modified Capabilities

None. The squash uses no comparator, inspector, or renderer.

## Impact

- **Config**: none. The squash takes its options as arguments.
- **Behavior**: none for existing users. Autogenerate does not import the new module.
- **Dependencies**: none new. The squash uses Alembic and `postgast.split()`, which the package already requires.
- **Public API**: `squash()`, `SquashResult`, and `SquashError`
- **Alembic compatibility**: the new revision is a normal revision file. Alembic needs no plugin to load or run it.
