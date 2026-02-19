## ADDED Requirements

### Requirement: Core runtime dependencies

The package SHALL declare `alembic` and `sqlalchemy>=2` as runtime dependencies in `pyproject.toml`. Only SQLAlchemy 2.x
is supported. The PostgreSQL driver is NOT a runtime dependency — users bring their own driver (psycopg, psycopg2,
asyncpg, etc.). The package SHALL require Python >= 3.10.

#### Scenario: Package installs with core runtime dependencies

- **WHEN** a user runs `uv add alembic-pg-autogen` (or `pip install alembic-pg-autogen`)
- **THEN** `alembic` and `sqlalchemy>=2` are installed as transitive dependencies
- **AND** no PostgreSQL driver is installed (the user provides their own)

#### Scenario: SQLAlchemy 1.x is rejected

- **WHEN** a user attempts to install the package with SQLAlchemy 1.x
- **THEN** the dependency resolver rejects the installation due to the `sqlalchemy>=2` constraint

#### Scenario: Python version constraint

- **WHEN** the package metadata is inspected
- **THEN** `requires-python` is set to `>=3.10,<4.0`
- **AND** CI tests against Python 3.10 through 3.14

#### Scenario: Import succeeds with dependencies available

- **WHEN** the package is installed in a Python environment
- **THEN** `import alembic_pg_autogen` succeeds without error
- **AND** `import alembic` and `import sqlalchemy` both succeed

### Requirement: Module structure for Alembic autogenerate extension

The package SHALL provide a module layout with separate private modules for the three Alembic autogenerate extension
points: comparators, operations, and renderers.

#### Scenario: Extension point modules exist

- **WHEN** the package is installed
- **THEN** the following modules exist and are importable:
  - `alembic_pg_autogen._compare` (future home of comparator functions)
  - `alembic_pg_autogen._render` (future home of render functions)
  - `alembic_pg_autogen._ops` (future home of MigrateOperation subclasses)

#### Scenario: Placeholder module and script entry point are removed

- **WHEN** the module structure is established
- **THEN** the file `alembic_pg_autogen.py` (the template placeholder) no longer exists
- **AND** the `[project.scripts]` section is removed from `pyproject.toml`

#### Scenario: Package **init** re-exports public API

- **WHEN** a user imports from `alembic_pg_autogen`
- **THEN** `__init__.py` re-exports public symbols from the extension point modules
- **AND** `__all__` lists all public symbols

### Requirement: Integration testing with ephemeral PostgreSQL

The package SHALL include test infrastructure that spins up an ephemeral PostgreSQL instance using Docker containers via
the `testcontainers` library.

#### Scenario: testcontainers and psycopg are dev dependencies

- **WHEN** a developer runs `make install`
- **THEN** `testcontainers[postgres]` and `psycopg[binary]` are installed as dev dependencies

#### Scenario: PostgreSQL fixture is available to tests

- **WHEN** an integration test needs a database connection
- **THEN** a pytest fixture provides a SQLAlchemy `Engine` connected to an ephemeral PostgreSQL container
- **AND** the container is started once per test session and torn down after all tests complete

#### Scenario: Integration test can execute SQL against PostgreSQL

- **WHEN** an integration test uses the PostgreSQL fixture
- **THEN** the test can create tables, insert rows, and query data against a real PostgreSQL instance
- **AND** each test session gets a clean database

### Requirement: Test organization with pytest markers

Tests SHALL be organized using pytest markers to distinguish unit tests from integration tests.

#### Scenario: Integration tests are marked

- **WHEN** a test requires Docker/PostgreSQL
- **THEN** it is decorated with `@pytest.mark.integration`

#### Scenario: Unit tests run without Docker

- **WHEN** a developer runs `make test-unit`
- **THEN** only tests NOT marked as `integration` are executed
- **AND** no Docker containers are started

#### Scenario: Full test suite runs all tests

- **WHEN** a developer runs `make test`
- **THEN** both unit tests and integration tests are executed

#### Scenario: Integration marker is registered

- **WHEN** pytest collects tests
- **THEN** the `integration` marker is registered in `pyproject.toml` (no unknown-marker warnings)

### Requirement: Project documentation

The project SHALL have documentation that replaces all template boilerplate with real project-specific content.

#### Scenario: README describes the project

- **WHEN** a user reads `README.md`
- **THEN** it describes alembic-pg-autogen as an Alembic autogenerate extension for PostgreSQL
- **AND** it includes installation instructions, basic usage guidance, and a link to development setup
- **AND** no template boilerplate text remains

#### Scenario: pyproject.toml metadata is updated

- **WHEN** the package metadata is inspected
- **THEN** the `description` field in `pyproject.toml` describes the project (not "changeme")
- **AND** classifiers include Alembic/database-relevant entries

### Requirement: Lint and type-check pass

All existing quality checks SHALL continue to pass after the foundation changes.

#### Scenario: make lint succeeds

- **WHEN** a developer runs `make lint`
- **THEN** codespell, ruff check, ruff format, and basedpyright all pass without errors

#### Scenario: make test succeeds

- **WHEN** a developer runs `make test`
- **THEN** all tests (unit and integration) pass
