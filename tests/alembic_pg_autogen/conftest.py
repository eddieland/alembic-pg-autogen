from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from testcontainers.postgres import PostgresContainer

from .alembic_helpers import AlembicProject


@pytest.fixture(scope="session")
def pg_engine() -> Generator[Engine]:
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        engine = create_engine(pg.get_connection_url())
        event.listen(engine, "checkout", _reset_search_path)
        yield engine
        engine.dispose()


def _reset_search_path(dbapi_conn: Any, _rec: Any, _proxy: Any) -> None:
    """Reset search_path on every pool checkout to prevent cross-test leaks."""
    cursor = dbapi_conn.cursor()
    cursor.execute("SET search_path TO public")
    cursor.close()


@pytest.fixture
def alembic_project(pg_engine: Engine, tmp_path: Path) -> Generator[AlembicProject]:
    project = AlembicProject(pg_engine, tmp_path)
    yield project
    project.teardown()
