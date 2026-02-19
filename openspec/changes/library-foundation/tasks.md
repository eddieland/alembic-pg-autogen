## 1. Dependencies and Project Metadata

- [ ] 1.1 Add runtime dependencies (`alembic`, `sqlalchemy>=2`) to `pyproject.toml` `[project] dependencies`
- [ ] 1.2 Add `testcontainers[postgres]` and `psycopg[binary]` to `pyproject.toml` `[dependency-groups] dev`
- [ ] 1.3 Update `pyproject.toml` `description` from "changeme" to a real project description
- [ ] 1.4 Add database/Alembic-relevant classifiers to `pyproject.toml`
- [ ] 1.5 Change `requires-python` from `>=3.11,<4.0` to `>=3.10,<4.0` and add Python 3.10 to classifiers
- [ ] 1.6 Update CI matrix in `.github/workflows/ci.yml` to include Python 3.10 (test 3.10–3.14)
- [ ] 1.7 Remove the `[project.scripts]` section from `pyproject.toml`
- [ ] 1.8 Run `uv sync --all-extras` to install new dependencies and verify lockfile updates

## 2. Module Structure

- [ ] 2.1 Delete `src/alembic_pg_autogen/alembic_pg_autogen.py` (template placeholder)
- [ ] 2.2 Create `src/alembic_pg_autogen/_compare.py` with empty module (brief docstring only)
- [ ] 2.3 Create `src/alembic_pg_autogen/_render.py` with empty module (brief docstring only)
- [ ] 2.4 Create `src/alembic_pg_autogen/_ops.py` with empty module (brief docstring only)
- [ ] 2.5 Rewrite `src/alembic_pg_autogen/__init__.py` to import from the three extension modules and define `__all__`

## 3. Test Infrastructure

- [ ] 3.1 Register the `integration` pytest marker in `pyproject.toml` `[tool.pytest.ini_options]`
- [ ] 3.2 Create `tests/conftest.py` with a session-scoped `pg_engine` fixture using `testcontainers` PostgreSQL
  container and SQLAlchemy `create_engine`
- [ ] 3.3 Add `test-unit` target to `Makefile` that runs `uv run pytest -m "not integration"`
- [ ] 3.4 Update `.PHONY` in `Makefile` to include `test-unit`

## 4. Tests

- [ ] 4.1 Replace `tests/test_placeholder.py` with `tests/test_import.py` — unit test verifying
  `import alembic_pg_autogen` and that the extension point modules are importable
- [ ] 4.2 Create `tests/test_pg_connection.py` — integration test (marked `@pytest.mark.integration`) that uses the
  `pg_engine` fixture to create a table, insert a row, and query it back

## 5. Documentation

- [ ] 5.1 Rewrite `README.md` — project description, installation, basic usage (how to configure Alembic to use this
  extension), development setup link
- [ ] 5.2 Update `docs/development.md` to mention Docker requirement for integration tests and the `make test-unit`
  target

## 6. Validation

- [ ] 6.1 Run `make lint` and fix any issues
- [ ] 6.2 Run `make test` and verify all tests pass (unit + integration)
- [ ] 6.3 Run `make test-unit` and verify only unit tests run (no Docker required)
