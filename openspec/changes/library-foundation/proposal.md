## Why

This project is currently an empty scaffold from a template. Before building any autogeneration logic, we need the foundational infrastructure that makes this a real Alembic extension library for PostgreSQL — core dependencies, proper documentation, a testing infrastructure that can exercise real database behavior, and the basic module structure that Alembic extensions require.

## What Changes

- Add core dependencies: `alembic`, `sqlalchemy`, and a PostgreSQL driver (`psycopg`)
- Replace template-generated documentation with real project documentation (README, usage concepts, contributing guide)
- Establish Docker-based integration testing infrastructure with ephemeral PostgreSQL containers
- Structure tests following the testing pyramid (unit > integration > end-to-end)
- Set up the basic module structure for an Alembic autogenerate extension (entry points, module layout) without implementing any autogen logic yet
- Add dev/test dependencies for Docker-based testing (`testcontainers` or similar)

## Capabilities

### New Capabilities

- `library-foundation`: Everything needed to go from template scaffold to real Alembic extension library — core dependencies (alembic, sqlalchemy, psycopg), module layout, Docker-based integration testing with ephemeral PostgreSQL, testing pyramid structure, and project documentation replacing template boilerplate.

### Modified Capabilities

_(none — no existing specs)_

## Impact

- **pyproject.toml**: New runtime dependencies (alembic, sqlalchemy, psycopg), new dev dependencies (testcontainers or similar), updated project description and metadata
- **src/alembic_pg_autogen/**: New module structure replacing the placeholder, though no functional logic yet
- **tests/**: New test infrastructure replacing the placeholder test, pytest fixtures for ephemeral PostgreSQL
- **docs/**: Rewritten documentation replacing template content
- **README.md**: Rewritten to describe this project
- **CI**: May need Docker-in-Docker or service containers for integration tests
- **Makefile**: Possible new targets for integration tests vs unit tests
