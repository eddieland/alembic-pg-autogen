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

## 5. Review of #53

- [x] 5.1 Add the preflight with a sentinel revision before the first upgrade. Verify that an `env.py` with its own URL
  leaves that database unchanged, also in a dry run.
- [x] 5.2 Keep owners in the dumps, and remove only `OWNER TO` the role that ran the squash. Verify with a table owned
  by another role.
- [x] 5.3 Compare roles, memberships, tablespaces, and settings of the server after each old-chain upgrade, and drop the
  roles and tablespaces that the run created. Verify with `CREATE ROLE` before and after the target.
- [x] 5.4 Compare column order and materialized view population, and append `REFRESH MATERIALIZED VIEW` in dependency
  order. Verify with an inherited table and two dependent materialized views.
- [x] 5.5 Compare row counts and sequences at the target, and add `allow_data_loss`. Verify that lost rows stop the
  squash by default.
- [x] 5.6 Pass a connection without a transaction. Verify with `autocommit_block()` and `transaction_per_migration`.
- [x] 5.7 Write the new file atomically before the deletes. Verify a failed write and a failed delete.
- [x] 5.8 Map copied files through their version location, and check the copy. Verify with a recursive version location.
- [x] 5.9 Remove settings that the server does not know, `COMMENT ON EXTENSION`, and only the matching `\restrict` pair.
  Quote the version table pattern, and rewrite `CREATE SCHEMA` of the version schema. Verify each with a test.
- [x] 5.10 Compare the two version tables at the heads, and use the declared branch labels. Verify a
  dependency-satisfied head and propagated labels.
- [x] 5.11 Catch connection errors in `main()`, read `pyproject.toml`, and add a CI job that runs the squash tests with
  `pg_dump` 18 against PostgreSQL 14, 16, 17, and 18. Make a missing `pg_dump` fail CI.

## 6. Second review of #53

- [x] 6.1 Scan the string literals of the replaced revisions for statements that change objects of the whole server, and
  never drop a role or a tablespace. Verify with a scan unit test, a `CREATE ROLE` chain, and dynamic SQL.
- [x] 6.2 Make the preflight sentinel create a marker table, and stop unless the marker exists. Verify with an `env.py`
  that catches errors.
- [x] 6.3 Add `extra_sql` and `--extra-sql`, run it in the comparison, and carry it forward. Verify with seed rows that
  a later revision reads, and a second squash.
- [x] 6.4 Compare `last_value` and `is_called` of each sequence relation. Verify with `ALTER SEQUENCE ... RESTART`.
- [x] 6.5 Order refreshes by `_RETURN` rules only, skip the order without populated views, and report cycles. Move the
  refreshes after the settings restore, refresh unpopulated dependencies, and move `OWNER TO` after the refresh. Verify
  each case.
- [x] 6.6 Keep `OWNER TO` when a replaced revision names a role, and remove `FOR ROLE` of the squash role from
  `ALTER DEFAULT PRIVILEGES`. Match the `\restrict` wrapper only in the preamble. Verify with tests.
- [x] 6.7 Refuse an import of a replaced module and a name collision. Report a failing new chain as `SquashError`.
  Verify with tests.
- [x] 6.8 Correct D3, D9, D12, and D13 in the design, add D14, and use `#.` lists in `docs/squash.rst`.

## 7. Third review of #53

- [x] 7.1 Record each database that an engine connects to during the preflight, and add a trip file to the sentinel.
  Verify with an `env.py` that loops over two databases.
- [x] 7.2 Keep `OWNER TO` the squash role only for objects that a replaced revision names, and never for `public`.
  Verify with two tables and one named owner.
- [x] 7.3 Anchor the scan at a statement start, skip docstrings and `comment` and `doc` values, and add
  `--allow-global-statements`. Verify each false positive from the review, and `CREATE ROLE` in a `DO` block.
- [x] 7.4 Match the `pg_dump` 14 form `ALTER TABLE` for a materialized view owner. Verify with a unit test and the CI
  run with `pg_dump` 14.
- [x] 7.5 Warn on carried `EXTRA_STATEMENTS`, and compare row digests of the tables that they touch. Verify with an
  `UPDATE` between two squashes.
- [x] 7.6 Add `pg_dump` 14 and 16 runs with the bootstrap role `postgres` to `squash-pg-versions`.
- [x] 7.7 Create the preflight marker with `IF NOT EXISTS`, so a tenant loop in one database passes the preflight.
