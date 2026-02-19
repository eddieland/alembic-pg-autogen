# alembic-pg-autogen

Alembic autogenerate extension for PostgreSQL. Extends Alembic's `--autogenerate` to detect and emit migrations for
PostgreSQL-specific objects that Alembic doesn't handle out of the box.

> **Note:** This project is in early development. The extension points are scaffolded but no autogeneration logic is
> implemented yet.

## Installation

```bash
pip install alembic-pg-autogen
```

Requires Python 3.10+ and SQLAlchemy 2.x. You provide your own PostgreSQL driver (psycopg, psycopg2, asyncpg, etc.).

## Usage

Configure Alembic's `env.py` to register this extension's comparators, operations, and renderers:

```python
# In your env.py
import alembic_pg_autogen  # noqa: F401
```

Then run `alembic revision --autogenerate` as usual. The extension hooks into Alembic's autogenerate pipeline to detect
PostgreSQL-specific changes.

## Development

See [docs/development.md](docs/development.md) for full setup instructions.

```bash
make install     # Install dependencies (uses uv)
make lint        # Run codespell, ruff, basedpyright
make test        # Run full test suite (requires Docker for integration tests)
make test-unit   # Run unit tests only (no Docker needed)
```

## License

MIT
