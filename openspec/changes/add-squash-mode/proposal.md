## Why

Squashing migrations is painful in `alembic_utils` because the autogenerator has no way to skip its own comparator. If a
function body references a table that's also being created in the squashed revision, the function ends up in the diff
before any tables exist, producing a migration that fails to run.

Until we land full dependency-aware ordering (see `add-dependency-ordering`), users need a pragmatic escape hatch: a
flag that disables this library's comparator entirely so the squash author can produce a tables-only revision first,
then a second revision containing every managed function and trigger.

## What Changes

- Add a `pg_autogen_skip` boolean opt to `context.configure()`. When truthy, `_compare_pg_objects` returns
  `PriorityDispatchResult.CONTINUE` immediately without inspecting the database or emitting any ops.
- Document the two-migration squash workflow in `docs/migrating.rst` (or a new `docs/squashing.rst`) — autogen with
  `pg_autogen_skip=True` to capture tables, then autogen again with the flag off to capture functions/triggers.

## Non-goals

- **Automating the two-step squash** — the squash author still runs `alembic revision --autogenerate` twice. We don't
  try to coordinate the two revisions or auto-link them.
- **Per-object skip** — the flag is all-or-nothing. Selective skipping is out of scope; the workflow is designed for the
  squash use case where you want zero managed-object ops in the first revision.
- **Replacing dependency ordering** — this is a stopgap for the cases where the topo sort can't help (e.g. squashes that
  drop and recreate tables that functions depend on).

## Capabilities

### Modified Capabilities

- `alembic-compare`: Add `pg_autogen_skip` opt. When set, the comparator short-circuits before any database inspection.

## Impact

- **Config**: New `pg_autogen_skip` key in `context.configure()` opts. Defaults to `False`.
- **Public API**: No new exports.
- **Performance**: When the flag is set, the comparator does zero work — useful in CI or local squashes where catalog
  inspection is wasted.
- **Docs**: New squashing section explaining the workflow and when to reach for it.
