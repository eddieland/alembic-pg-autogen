"""End-to-end tests verifying extension-owned objects are excluded from inspection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

from alembic_pg_autogen import inspect_functions, inspect_triggers

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy.engine import Engine


@pytest.fixture(scope="session")
def postgis_engine(request: pytest.FixtureRequest) -> Generator[Engine]:
    """Session-scoped engine backed by a PostGIS-enabled PostgreSQL container.

    The ``postgis/postgis`` image ships with PostGIS pre-installed in the ``public`` schema, so no ``CREATE EXTENSION``
    is needed.
    """
    pg_version: str = request.config.getoption("--pg-version")
    with PostgresContainer(f"postgis/postgis:{pg_version}-3.5", driver="psycopg") as pg:
        engine = create_engine(pg.get_connection_url())
        yield engine
        engine.dispose()


@pytest.mark.postgis
class TestExtensionFunctionsExcluded:
    """Extension-owned functions are excluded while user functions are retained."""

    def test_user_function_returned_no_postgis_functions(self, postgis_engine: Engine) -> None:
        with postgis_engine.begin() as conn:
            conn.execute(text("CREATE FUNCTION my_user_func() RETURNS integer LANGUAGE SQL AS $$ SELECT 1; $$"))
            try:
                results = inspect_functions(conn, ["public"])
            finally:
                conn.execute(text("DROP FUNCTION IF EXISTS my_user_func()"))

        names = {f.name for f in results}
        assert "my_user_func" in names
        st_names = {f.name for f in results if f.name.startswith("st_")}
        assert st_names == set(), f"Extension functions leaked: {st_names}"

    def test_extension_owned_function_in_catalog_but_excluded(self, postgis_engine: Engine) -> None:
        with postgis_engine.begin() as conn:
            # Find an extension-owned regular function in public via pg_depend
            ext_func_name = conn.execute(
                text("""\
                    SELECT p.proname
                    FROM pg_catalog.pg_proc p
                    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                    JOIN pg_catalog.pg_depend d ON d.classid = 'pg_catalog.pg_proc'::regclass
                                                AND d.objid = p.oid
                                                AND d.deptype = 'e'
                    WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
                    LIMIT 1
                """),
            ).scalar()
            assert ext_func_name is not None, "PostGIS should install extension-owned functions in public"

            results = inspect_functions(conn, ["public"])

        names = {f.name for f in results}
        assert ext_func_name not in names, f"{ext_func_name} should be excluded from inspect_functions"


@pytest.mark.postgis
class TestExtensionTriggersExcluded:
    """User triggers are retained when an extension is present in the same schema."""

    def test_user_trigger_returned_with_extension_present(self, postgis_engine: Engine) -> None:
        with postgis_engine.begin() as conn:
            conn.execute(text("CREATE TABLE test_ext_table (id serial PRIMARY KEY, name text)"))
            conn.execute(
                text("""\
                    CREATE FUNCTION my_trigger_func()
                    RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN RETURN NEW; END; $$
                """)
            )
            conn.execute(
                text("""\
                    CREATE TRIGGER my_test_trigger
                    BEFORE INSERT ON test_ext_table
                    FOR EACH ROW EXECUTE FUNCTION my_trigger_func()
                """)
            )
            try:
                results = inspect_triggers(conn, ["public"])
            finally:
                conn.execute(text("DROP TABLE IF EXISTS test_ext_table CASCADE"))
                conn.execute(text("DROP FUNCTION IF EXISTS my_trigger_func()"))

        trigger_names = {t.trigger_name for t in results}
        assert "my_test_trigger" in trigger_names
