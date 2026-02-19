"""Test helpers for creating isolated Alembic projects against real PostgreSQL."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final

from alembic.config import Config
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy import Connection
    from sqlalchemy.engine import Engine


class AlembicProject:
    """An isolated Alembic project directory backed by a unique PostgreSQL schema.

    Provides helpers for executing SQL and obtaining connections with the
    ``search_path`` set to the isolated schema.  Intended for integration tests
    that create PG objects (functions, triggers, tables) and verify inspection
    or autogenerate results.
    """

    def __init__(self, engine: Engine, tmp_path: Path) -> None:
        """Create an isolated Alembic project with a unique PostgreSQL schema."""
        self._engine: Final = engine
        self._tmp_path: Final = tmp_path
        self._schema: Final = f"test_{uuid.uuid4().hex[:12]}"
        self._setup_schema()
        self._setup_directory()

    @property
    def schema(self) -> str:
        """The unique PostgreSQL schema name for this project."""
        return self._schema

    @property
    def config(self) -> Config:
        """Return an Alembic ``Config`` pointing at the project directory."""
        cfg = Config(str(self._tmp_path / "alembic.ini"))
        cfg.set_main_option("script_location", str(self._tmp_path / "alembic"))
        cfg.attributes["connection"] = self._engine
        return cfg

    def execute(self, sql: str) -> None:
        """Run raw SQL with ``search_path`` set to the isolated schema."""
        with self.connect() as conn:
            conn.execute(text(sql))
            conn.commit()

    @contextmanager
    def connect(self) -> Generator[Connection]:
        """Yield a connection with ``search_path`` set to the isolated schema."""
        with self._engine.connect() as conn:
            conn.execute(text(f"SET search_path TO {self._schema}"))
            try:
                yield conn
            finally:
                conn.execute(text("SET search_path TO public"))

    def teardown(self) -> None:
        """Drop the isolated schema and all objects within it."""
        with self._engine.connect() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {self._schema} CASCADE"))
            conn.commit()

    def _setup_schema(self) -> None:
        with self._engine.connect() as conn:
            conn.execute(text(f"CREATE SCHEMA {self._schema}"))
            conn.commit()

    def _setup_directory(self) -> None:
        alembic_dir = self._tmp_path / "alembic"
        alembic_dir.mkdir()
        (alembic_dir / "versions").mkdir()
        (alembic_dir / "env.py").write_text(_ENV_PY)
        (alembic_dir / "script.py.mako").write_text(_SCRIPT_MAKO)
        (self._tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = alembic\n")


_ENV_PY = """\
from alembic import context

config = context.config
connection = config.attributes["connection"]
target_metadata = config.attributes.get("target_metadata")


def run_migrations_online() -> None:
    with connection.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
"""

_SCRIPT_MAKO = """\
\"\"\"${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

\"\"\"
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
"""
