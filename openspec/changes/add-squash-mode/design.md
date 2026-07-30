## Context

`_compare_pg_objects` currently runs unconditionally whenever either `pg_functions` or `pg_triggers` is present in
`autogen_context.opts`. There's no way to tell the comparator "the user knows what they're doing — stand down for this
revision."

Squashing typically follows this flow:

1. Stamp the database to a clean baseline.
1. Drop all managed objects + tables.
1. Run `alembic upgrade head` against the empty database to recreate everything via the existing migration chain.
1. Generate one or more new revisions that capture the result.

Step 4 is where the order-of-operations problem surfaces. A function that references a table can't be canonicalized
during step 4 because the table will only exist in the squashed revision's `op.create_table()` call — but the
canonicalize savepoint runs against the *current* (empty) database, so the function DDL fails immediately.

## Goals / Non-Goals

**Goals:**

- Provide a single-line opt-in to disable this library's comparator for one autogen run.
- Keep the implementation tiny — one early return in `_compare_pg_objects`.
- Document the squash workflow so users know when to use the flag.

**Non-Goals:**

- Smart squash detection. The flag is explicit; we don't try to infer when a squash is happening.
- Splitting one autogen invocation into two revisions. The user controls revision boundaries.

## Decisions

### D1: Opt name — `pg_autogen_skip`

Named to mirror the existing `pg_functions` / `pg_triggers` conventions and to make it grep-able. The `_skip` suffix is
clearer than alternatives like `pg_autogen_disable` (sounds permanent) or `pg_autogen_pass` (ambiguous with HTTP-style
"pass through").

**Alternative considered:** Environment variable (`ALEMBIC_PG_AUTOGEN_SKIP=1`). Rejected — `context.configure()` opts
are the established surface; env-var configuration would be a new pattern for users to learn and harder to scope to a
single revision in `env.py`.

### D2: Short-circuit position — before `_resolve_ddl`

The early return happens at the top of `_compare_pg_objects`, before any DDL parsing or database inspection. This
guarantees the flag is cheap (zero queries, zero parsing) and that bad DDL in `pg_functions` won't surface during a
skipped run.

### D3: Logging

Emit one `log.info` line when the flag is honored (`"alembic-pg-autogen: pg_autogen_skip is set, skipping comparator"`).
This is loud enough that a user who left the flag set by accident will notice in autogen output.

## Risks / Trade-offs

**[User leaves flag set permanently]** → Future autogen runs silently produce empty diffs for managed objects, drift
goes undetected. **Mitigation:** The `log.info` line during every skipped autogen makes this visible. The flag is
conventionally set in a code branch (e.g. `if os.environ.get("SQUASHING")`) rather than hardcoded.

**[Two-step workflow is fiddly]** → Squashers must remember to run autogen twice and configure the flag correctly.
**Mitigation:** Document explicitly with a worked example. The dependency-ordering change reduces how often this
workflow is needed.

## Open Questions

_(none — single-flag scope is small and well-defined)_
