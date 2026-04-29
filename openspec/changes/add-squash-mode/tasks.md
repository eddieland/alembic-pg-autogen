## 1. Comparator short-circuit

- [ ] 1.1 Add an early return at the top of `_compare_pg_objects` in `src/alembic_pg_autogen/compare.py` that checks
  `opts.get("pg_autogen_skip")`; if truthy, log at `info` level and return `PriorityDispatchResult.CONTINUE`.
- [ ] 1.2 Verify the early return runs before `_resolve_ddl` and `inspect_functions` so a skipped run does no parsing
  and no catalog queries.

## 2. Tests

- [ ] 2.1 Add a unit test in `tests/alembic_pg_autogen/test_autogenerate.py` (or equivalent) verifying that when
  `pg_autogen_skip=True` is passed via `autogen_context.opts`, no ops are appended to `upgrade_ops.ops` even when
  `pg_functions` and `pg_triggers` are also set.
- [ ] 2.2 Add a unit test verifying that `pg_autogen_skip=False` (or absent) preserves current behavior.
- [ ] 2.3 Add a test verifying the `info`-level log message is emitted exactly once per skipped run.

## 3. Documentation

- [ ] 3.1 Add a "Squashing migrations" section to `docs/migrating.rst` (or a new `docs/squashing.rst` linked from
  `docs/index.rst`) describing the two-revision workflow: (a) set `pg_autogen_skip=True` in `env.py`, run
  `alembic revision --autogenerate -m "squash tables"`; (b) unset the flag, run
  `alembic revision --autogenerate -m "squash functions and triggers"`; (c) commit both revisions in order.
- [ ] 3.2 Add a code snippet showing a typical conditional flag in `env.py` (e.g.
  `pg_autogen_skip=os.environ.get("ALEMBIC_PG_AUTOGEN_SKIP") == "1"`).
- [ ] 3.3 Cross-link the section from the `add-dependency-ordering` docs once that change lands, noting that squash mode
  is the escape hatch when the dependency resolver can't help.

## 4. Validation

- [ ] 4.1 Run `make lint` and fix any issues.
- [ ] 4.2 Run `make test` and verify all existing + new tests pass.
