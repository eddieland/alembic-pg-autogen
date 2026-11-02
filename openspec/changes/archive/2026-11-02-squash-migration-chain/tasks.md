# Tasks

## 1. Planning

- [x] 1.1 Create `src/alembic_pg_autogen/squash.py` with `SquashError`, `SquashResult`, and `_plan()`. `_plan()`
  resolves the target, collects the replaced set through `down_revision` and `depends_on`, and refuses a side branch or
  a branch label. Verify with the `TestPlan` unit tests in `tests/alembic_pg_autogen/test_squash.py`.

## 2. Dump and render

- [x] 2.1 Add `clean_dump()`, `changed_settings()`, and `render_revision()`. Verify with the `TestCleanDump` and
  `TestRender` unit tests, including a comment line inside a function body and the branch labels of the target.
- [x] 2.2 Add `_scan_replaced_files()` for data statements and global objects. Verify with a unit test.

## 3. Scratch databases and checks

- [x] 3.1 Add the scratch database lifecycle, the `pg_dump` runner, and the upgrade with the version table check. Verify
  that a failed `pg_dump` and an `env.py` that ignores the scratch database raise `SquashError` and leave no scratch
  database.
- [x] 3.2 Copy the script directory without the replaced files, write the new revision into the copy, and compare the
  dumps at the target and at the heads. Verify with an integration test whose schema differs at the heads.
- [x] 3.3 Write the new file and delete the replaced files after both checks pass, unless `dry_run` is set. Verify with
  integration tests for a full chain, a dry run, a squash of every revision, and a database at the target that upgrades
  to head without `stamp`.

## 4. Command line and documentation

- [x] 4.1 Add `main()` and the `python -m` entry point. Verify with tests for a failed check, a dry run, and a project
  whose `env.py` reads only `sqlalchemy.url` and whose versions are outside the script directory.
- [x] 4.2 Document the squash in `README.md` and `docs/squash.rst`. Verify with
  `uv run mdformat --check README.md CLAUDE.md openspec/`.
- [x] 4.3 Run `make lint` and the full test suite against PostgreSQL, and verify that every check passes.
