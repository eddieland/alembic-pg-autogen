from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from testcontainers.postgres import PostgresContainer

from .alembic_helpers import AlembicProject

PG_VERSION_DEFAULT = "14"

#: Set this to a PostgreSQL URL (e.g. ``postgresql+psycopg://user:pw@localhost/db``) to run the integration tests
#: against an existing server instead of a throwaway Docker container.
PG_URL_ENV_VAR = "ALEMBIC_PG_AUTOGEN_TEST_URL"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add ``--pg-version`` CLI option for selecting the PostgreSQL Docker image tag."""
    parser.addoption(
        "--pg-version",
        default=PG_VERSION_DEFAULT,
        help=f"PostgreSQL major version for testcontainers (default: {PG_VERSION_DEFAULT})",
    )


@pytest.fixture(scope="session")
def pg_engine(request: pytest.FixtureRequest) -> Generator[Engine]:
    url = os.environ.get(PG_URL_ENV_VAR)
    if url:
        engine = create_engine(url)
        event.listen(engine, "checkout", _reset_search_path)
        yield engine
        engine.dispose()
        return

    pg_version: str = request.config.getoption("--pg-version")
    with PostgresContainer(f"postgres:{pg_version}", driver="psycopg") as pg:
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
