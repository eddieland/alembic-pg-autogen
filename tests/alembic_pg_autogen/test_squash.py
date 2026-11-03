"""Tests for ``alembic_pg_autogen.squash``.

The unit tests need no database.  The integration tests create scratch databases on the test server and need a
``pg_dump`` binary on ``PATH``.  Set ``ALEMBIC_PG_AUTOGEN_REQUIRE_PG_DUMP=1`` to make a missing binary fail the tests
instead of skipping them.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.pool import NullPool

from alembic_pg_autogen.squash import (
    _MATVIEW_OWNER_TO,
    SquashError,
    _check_before_run,
    _config_files,
    _global_statements,
    _libpq_uri,
    _plan,
    _strip_leading_comments,
    _strip_restrict,
    _write,
    changed_settings,
    clean_dump,
    main,
    render_revision,
    squash,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping
    from typing import Final

    from sqlalchemy.engine import Engine

if shutil.which("pg_dump") is None and os.environ.get("ALEMBIC_PG_AUTOGEN_REQUIRE_PG_DUMP"):
    pytest.fail("ALEMBIC_PG_AUTOGEN_REQUIRE_PG_DUMP is set, but pg_dump is not on PATH", pytrace=False)

needs_pg_dump = pytest.mark.skipif(shutil.which("pg_dump") is None, reason="pg_dump is not on PATH")

# The cookbook recipe "Sharing a Connection across one or more programmatic migration commands", plus the options
# that some tests set through ``config.attributes``.
COOKBOOK_ENV = """\
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

config = context.config
options = {
    key: config.attributes[key]
    for key in ("transaction_per_migration", "version_table", "version_table_schema")
    if key in config.attributes
}


def run(connection):
    schema = options.get("version_table_schema")
    if schema:
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    context.configure(connection=connection, **options)
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is None:
    engine = create_engine(config.get_main_option("sqlalchemy.url"), poolclass=NullPool)
    with engine.connect() as connection:
        run(connection)
        connection.commit()
    engine.dispose()
else:
    run(connection)
"""

URL_ONLY_ENV = """\
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

config = context.config
engine = create_engine(config.get_main_option("sqlalchemy.url"), poolclass=NullPool)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
"""

DECOY_ENV = """\
import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

config = context.config
config.set_main_option("sqlalchemy.url", os.environ["SQUASH_TEST_DECOY_URL"].replace("%", "%%"))
engine = create_engine(config.get_main_option("sqlalchemy.url"), poolclass=NullPool)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
"""


SWALLOW_ENV = """\
import logging
import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

config = context.config
engine = create_engine(os.environ["SQUASH_TEST_DECOY_URL"], poolclass=NullPool)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        try:
            context.run_migrations()
        except Exception:
            logging.getLogger("env").exception("migration failed, continuing")
engine.dispose()
"""


TWO_TARGET_ENV = """\
import logging
import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

config = context.config
for url in (config.get_main_option("sqlalchemy.url"), os.environ["SQUASH_TEST_DECOY_URL"]):
    engine = create_engine(url, poolclass=NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        try:
            with context.begin_transaction():
                context.run_migrations()
        except Exception:
            logging.getLogger("env").exception("migration failed for one database, continuing")
        connection.commit()
    engine.dispose()
"""


TENANT_LOOP_ENV = """\
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

config = context.config
engine = create_engine(config.get_main_option("sqlalchemy.url"), poolclass=NullPool)
for schema in ("tenant_a", "tenant_b"):
    with engine.connect() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        connection.exec_driver_sql(f"SET search_path TO {schema}, public")
        context.configure(connection=connection, version_table_schema=schema)
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()
engine.dispose()
"""


def _rev(rev: str, down: str | tuple[str, ...] | None, body: str = "pass", *, extra: str = "") -> str:
    """Render a minimal revision file."""
    return (
        f"import sqlalchemy as sa\nfrom alembic import op\n\nrevision = {rev!r}\ndown_revision = {down!r}\n"
        f"branch_labels = None\ndepends_on = None\n{extra}\n\n\ndef upgrade():\n"
        + textwrap.indent(textwrap.dedent(body).strip(), "    ")
        + "\n\n\ndef downgrade():\n    raise NotImplementedError\n"
    )


def _labelled(rev: str, down: str | None, label: str) -> str:
    return _rev(rev, down).replace("branch_labels = None", f"branch_labels = ({label!r},)")


CHAIN: Mapping[str, str] = {
    "r01": _rev(
        "r01",
        None,
        """
        op.execute("CREATE EXTENSION IF NOT EXISTS citext")
        op.execute("CREATE TABLE account (id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY, email citext UNIQUE)")
        """,
    ),
    "r02": _rev(
        "r02",
        "r01",
        '''
        op.execute("""
            CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                -- a comment line inside the body
                NEW.updated_at = now();
                RETURN NEW;
            END $$
        """)
        op.execute("CREATE TABLE post (id serial PRIMARY KEY, account_id int REFERENCES account, updated_at timestamptz)")
        op.execute("CREATE TRIGGER post_touch BEFORE UPDATE ON post FOR EACH ROW EXECUTE FUNCTION touch()")
        ''',
    ),
    "r03": _rev(
        "r03",
        "r02",
        """
        op.add_column("account", sa.Column("legacy", sa.Integer))
        op.add_column("account", sa.Column("name", sa.Text))
        op.drop_column("account", "legacy")
        op.create_index("account_name_idx", "account", ["name"], postgresql_where=sa.text("name IS NOT NULL"))
        """,
    ),
    "r04": _rev(
        "r04",
        "r03",
        """
        op.execute("CREATE FUNCTION post_count() RETURNS bigint LANGUAGE sql AS 'SELECT count(*) FROM public.post'")
        op.execute("CREATE VIEW stats AS SELECT post_count() AS posts")
        op.execute("CREATE MATERIALIZED VIEW post_ids AS SELECT id FROM post")
        op.execute("CREATE MATERIALIZED VIEW post_id_count AS SELECT count(*) AS n FROM post_ids")
        """,
    ),
    "r05": _rev(
        "r05",
        "r04",
        """
        op.execute("CREATE TYPE mood AS ENUM ('sad', 'ok')")
        op.execute("ALTER TYPE mood ADD VALUE 'happy'")
        """,
    ),
    "r06": _rev(
        "r06",
        "r05",
        """
        op.rename_table("post", "article")
        account = sa.table("account", sa.column("email"))
        op.execute(account.insert().values(email="seed@example.com"))
        """,
    ),
    "r07": _rev(
        "r07",
        "r06",
        r"""
        op.execute(sa.text(r"ALTER TABLE account ADD CONSTRAINT name_shape CHECK (name <> 'a \:b' AND name NOT LIKE '%\\%')"))
        op.execute(sa.text(r"COMMENT ON TABLE account IS E'back\\slash'"))
        """,
    ),
    "r08": _rev("r08", "r07", 'op.create_table("audit", sa.Column("id", sa.Integer, primary_key=True))'),
    "r09": _rev("r09", "r08", 'op.create_index("article_account_idx", "article", ["account_id"])'),
}


class Project:
    """An Alembic project in a temporary directory, with one file per revision."""

    def __init__(
        self,
        root: Path,
        revisions: Mapping[str, str],
        *,
        env: str = COOKBOOK_ENV,
        versions: str | None = None,
        recursive: bool = False,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        """Write ``env.py``, ``alembic.ini``, and the revision files under ``root``.

        ``versions`` names a version location relative to ``root``.  ``None`` uses ``migrations/versions``.
        """
        self.root: Final = root
        self.script_dir: Final = root / "migrations"
        self.versions: Final = root / versions if versions else self.script_dir / "versions"
        self.attributes: Final = dict(attributes or {})
        (self.script_dir / "versions").mkdir(parents=True)
        self.versions.mkdir(parents=True, exist_ok=True)
        (self.script_dir / "env.py").write_text(env)
        (self.script_dir / "script.py.mako").write_text("")
        self.ini: Final = root / "alembic.ini"
        lines = ["[alembic]", "script_location = %(here)s/migrations", "path_separator = os"]
        if versions:
            lines.append(f"version_locations = %(here)s/{versions}")
        if recursive:
            lines.append("recursive_version_locations = true")
        self.ini.write_text("\n".join(lines) + "\n")
        for name, body in revisions.items():
            self.write(name, body)

    def write(self, name: str, body: str) -> Path:
        path = self.versions / f"{name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return path

    def config(self, url: sa.URL | None = None) -> Config:
        cfg = Config(str(self.ini), attributes=dict(self.attributes))
        if url is not None:
            cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
        return cfg

    def files(self) -> set[str]:
        return {str(p.relative_to(self.versions)) for p in self.versions.rglob("*.py")}


@pytest.fixture
def make_role(pg_engine: Engine) -> Generator[Callable[[], str]]:
    """Create roles on the test server and drop them after the test."""
    admin = sa.create_engine(pg_engine.url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    created: list[str] = []

    def factory(name: str | None = None) -> str:
        """Create a role, or with ``name``, only register a role that a test creates, for the drop."""
        if name is None:
            name = f"sq_role_{uuid.uuid4().hex[:8]}"
            with admin.connect() as conn:
                conn.execute(sa.text(f'CREATE ROLE "{name}"'))
        created.append(name)
        return name

    yield factory
    with admin.connect() as conn:
        for name in created:
            conn.execute(sa.text(f'DROP ROLE IF EXISTS "{name}"'))
    admin.dispose()


@pytest.fixture
def make_db(pg_engine: Engine, make_role: Callable[..., str]) -> Generator[Callable[[], sa.URL]]:
    """Create empty databases on the test server and drop them after the test, before the roles."""
    del make_role
    admin = sa.create_engine(pg_engine.url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    created: list[str] = []

    def factory() -> sa.URL:
        name = f"squash_test_{uuid.uuid4().hex[:10]}"
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
        created.append(name)
        return pg_engine.url.set(database=name)

    yield factory
    with admin.connect() as conn:
        for name in created:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _scalars(url: sa.URL, sql: str) -> set[Any]:
    engine = sa.create_engine(url, poolclass=NullPool)
    with engine.connect() as conn:
        found = set(conn.execute(sa.text(sql)).scalars())
    engine.dispose()
    return found


def _version(url: sa.URL, table: str = "alembic_version") -> set[str]:
    return _scalars(url, f"SELECT version_num FROM {table}")


def _tables(url: sa.URL) -> set[str]:
    return _scalars(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")


def _leftovers(pg_engine: Engine) -> list[str]:
    """Return scratch databases and test roles that a squash left on the server."""
    with pg_engine.connect() as conn:
        databases = conn.execute(sa.text(r"SELECT datname FROM pg_database WHERE datname ~ '^squash_[0-9a-f]{12}_'"))
        roles = conn.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname LIKE 'sq\\_chain\\_%'"))
        return list(databases.scalars()) + list(roles.scalars())


class TestCleanDump:
    def test_removes_meta_commands_comments_and_unsafe_statements(self) -> None:
        dump = textwrap.dedent(
            """\
            --
            -- PostgreSQL database dump
            --

            \\restrict abc123

            SET statement_timeout = 0;
            SET transaction_timeout = 0;
            SET client_encoding = 'UTF8';
            SELECT pg_catalog.set_config('search_path', '', false);

            CREATE SCHEMA meta;

            CREATE EXTENSION IF NOT EXISTS citext WITH SCHEMA public;

            COMMENT ON EXTENSION citext IS 'data type for case-insensitive character strings';

            --
            -- Name: f(); Type: FUNCTION; Schema: public; Owner: app
            --

            CREATE FUNCTION public.f() RETURNS integer
                LANGUAGE plpgsql
                AS $$
            BEGIN
            -- kept, because it is inside the body
            RETURN 1;
            END $$;

            ALTER FUNCTION public.f() OWNER TO app;

            ALTER TABLE public.t OWNER TO "Other Owner";

            \\unrestrict abc123
            """
        )
        statements = clean_dump(
            dump, known_settings={"statement_timeout", "search_path"}, owner="app", version_schema="meta"
        )
        assert statements[0] == "SET statement_timeout = 0"
        assert statements[1] == "SELECT pg_catalog.set_config('search_path', '', false)"
        assert statements[2] == "CREATE SCHEMA IF NOT EXISTS meta"
        assert statements[3] == "CREATE EXTENSION IF NOT EXISTS citext WITH SCHEMA public"
        assert statements[4].startswith("CREATE FUNCTION public.f()")
        assert "-- kept, because it is inside the body" in statements[4]
        assert statements[5] == 'ALTER TABLE public.t OWNER TO "Other Owner"'
        assert len(statements) == 6

    def test_keeps_every_statement_without_options(self) -> None:
        statements = clean_dump("SET transaction_timeout = 0;\nALTER TABLE t OWNER TO app;\nCREATE SCHEMA meta;\n")
        assert statements == ["SET transaction_timeout = 0", "ALTER TABLE t OWNER TO app", "CREATE SCHEMA meta"]

    def test_default_privileges_of_the_squash_role_apply_to_the_migrating_role(self) -> None:
        dump = (
            "ALTER DEFAULT PRIVILEGES FOR ROLE app IN SCHEMA public GRANT SELECT ON TABLES TO reader;\n"
            "ALTER DEFAULT PRIVILEGES FOR ROLE other GRANT SELECT ON TABLES TO reader;\n"
        )
        assert clean_dump(dump, default_privileges_role="app") == [
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO reader",
            "ALTER DEFAULT PRIVILEGES FOR ROLE other GRANT SELECT ON TABLES TO reader",
        ]

    def test_strip_restrict_without_a_wrapper_keeps_a_body_line(self) -> None:
        dump = "SET x = 1;\nCREATE FUNCTION f() AS $$\n\\restrict keepme\nend $$;\n\\unrestrict keepme\n"
        assert _strip_restrict(dump) == dump

    def test_unparsable_dump(self) -> None:
        with pytest.raises(SquashError, match="postgast cannot split the pg_dump output"):
            clean_dump("CREATE TABLE (;")

    def test_strip_restrict_removes_only_the_matching_pair(self) -> None:
        body = "CREATE FUNCTION f() AS $$\n\\restrict keepme\r\nend $$;\n"
        dump = f"-- head\n\\restrict K1\n\n{body}\n\\unrestrict K1\n"
        assert _strip_restrict(dump) == f"-- head\n\n{body}\n"
        assert _strip_restrict(body) == body

    def test_strip_leading_comments_keeps_inner_lines(self) -> None:
        assert _strip_leading_comments("\n-- a\n\n--\nSELECT 1\n-- b\n") == "SELECT 1\n-- b"
        assert _strip_leading_comments("-- only\n") == ""

    def test_changed_settings(self) -> None:
        statements = [
            "SET statement_timeout = 0",
            "SELECT pg_catalog.set_config('search_path', '', false)",
            "CREATE TABLE public.t (id integer)",
            "SET default_tablespace = ''",
            "SET statement_timeout = 0",
        ]
        assert changed_settings(statements) == ["statement_timeout", "search_path", "default_tablespace"]


class TestRender:
    def test_rendered_revision_is_a_plain_alembic_revision(self, tmp_path: Path) -> None:
        project = Project(tmp_path, {"r01": _rev("r01", None), "r02": _rev("r02", "r01")})
        script = ScriptDirectory.from_config(project.config()).get_revision("r02")
        statements = ["SET search_path = ''", "CREATE TABLE public.t (note text CHECK (note <> 'a :b'))"]

        source = render_revision(script, ["r01", "r02"], statements)
        namespace: dict[str, Any] = {}
        exec(compile(source, "r02_squashed.py", "exec"), namespace)

        assert namespace["revision"] == "r02"
        assert namespace["down_revision"] is None
        assert namespace["branch_labels"] is None
        assert namespace["squashed_revisions"] == ("r01", "r02")
        assert namespace["SETTINGS"] == ("search_path",)
        assert namespace["STATEMENTS"] == tuple(statements)
        assert "import alembic_pg_autogen" not in source
        assert "from alembic_pg_autogen" not in source
        with pytest.raises(NotImplementedError, match="squashes 2 revisions"):
            namespace["downgrade"]()

    def test_target_branch_labels_carry_over(self, tmp_path: Path) -> None:
        project = Project(tmp_path, {"r01": _rev("r01", None), "r02": _labelled("r02", "r01", "core")})
        script = ScriptDirectory.from_config(project.config()).get_revision("r02")
        assert "branch_labels = ('core',)" in render_revision(script, ["r01", "r02"], [])


class TestPlan:
    def _plan(self, tmp_path: Path, revisions: Mapping[str, str], target: str) -> list[str]:
        script_dir = ScriptDirectory.from_config(Project(tmp_path, revisions).config())
        return [s.revision for s in _plan(script_dir, target).replaced]

    def test_linear_chain_from_base_to_target(self, tmp_path: Path) -> None:
        revisions = {f"r0{i}": _rev(f"r0{i}", f"r0{i - 1}" if i > 1 else None) for i in range(1, 5)}
        assert self._plan(tmp_path, revisions, "r03") == ["r01", "r02", "r03"]

    def test_merge_before_target_is_replaced(self, tmp_path: Path) -> None:
        revisions = {
            "a": _rev("a", None),
            "b1": _rev("b1", "a"),
            "b2": _rev("b2", "a"),
            "m": _rev("m", ("b1", "b2")),
            "c": _rev("c", "m"),
        }
        replaced = self._plan(tmp_path, revisions, "m")
        assert replaced[0] == "a"
        assert replaced[-1] == "m"
        assert set(replaced) == {"a", "b1", "b2", "m"}

    def test_prefix_resolves(self, tmp_path: Path) -> None:
        revisions = {"abc1": _rev("abc1", None), "def2": _rev("def2", "abc1")}
        assert self._plan(tmp_path, revisions, "def") == ["abc1", "def2"]

    def test_revision_after_target_naming_a_removed_parent(self, tmp_path: Path) -> None:
        revisions = {
            "r01": _rev("r01", None),
            "r02": _rev("r02", "r01"),
            "r03": _rev("r03", "r02"),
            "x": _rev("x", "r01"),
        }
        with pytest.raises(SquashError, match=r"Revision x names r01, which the squash removes"):
            self._plan(tmp_path, revisions, "r02")

    def test_depends_on_a_removed_revision(self, tmp_path: Path) -> None:
        other = _rev("x", None).replace("depends_on = None", "depends_on = 'r01'")
        revisions = {"r01": _rev("r01", None), "r02": _rev("r02", "r01"), "x": other}
        with pytest.raises(SquashError, match=r"Revision x names r01"):
            self._plan(tmp_path, revisions, "r02")

    def test_depends_on_the_target_is_allowed(self, tmp_path: Path) -> None:
        other = _rev("x", None).replace("depends_on = None", "depends_on = 'r02'")
        revisions = {"r01": _rev("r01", None), "r02": _rev("r02", "r01"), "x": other}
        assert self._plan(tmp_path, revisions, "r02") == ["r01", "r02"]

    def test_branch_label_declared_on_a_removed_revision(self, tmp_path: Path) -> None:
        revisions = {"r01": _labelled("r01", None, "core"), "r02": _rev("r02", "r01")}
        with pytest.raises(SquashError, match=r"Revision r01 declares branch labels \['core'\]"):
            self._plan(tmp_path, revisions, "r02")

    def test_propagated_labels_do_not_count(self, tmp_path: Path) -> None:
        revisions = {
            "r01": _rev("r01", None),
            "r02": _rev("r02", "r01"),
            "r03": _labelled("r03", "r02", "core"),
            "r04": _labelled("r04", "r03", "later"),
        }
        assert self._plan(tmp_path / "a", revisions, "r03") == ["r01", "r02", "r03"]
        assert self._plan(tmp_path / "b", revisions, "r02") == ["r01", "r02"]

    def test_unknown_target(self, tmp_path: Path) -> None:
        with pytest.raises(SquashError, match="Cannot find revision 'nope'"):
            self._plan(tmp_path, {"r01": _rev("r01", None)}, "nope")

    def test_base_is_not_a_target(self, tmp_path: Path) -> None:
        with pytest.raises(SquashError, match="'base' names no revision"):
            self._plan(tmp_path, {"r01": _rev("r01", None)}, "base")


class TestChecksBeforeRun:
    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "rev.py"
        path.write_text(body)
        return path

    def test_global_statement_scan(self, tmp_path: Path) -> None:
        body = textwrap.dedent(
            '''\
            """CREATE ROLE reader in the docstring does not count."""
            # CREATE ROLE reader in a comment does not count.
            import sqlalchemy as sa
            from alembic import op

            LABELS = ("create user", "drop user", "alter role")


            def upgrade():
                """Grant admin access to API keys."""
                op.execute("GRANT SELECT ON t TO reader")
                op.execute("CREATE USER MAPPING FOR app SERVER s")
                op.execute("COMMENT ON TABLE t IS 'Grant tokens to users'")
                op.add_column("t", sa.Column("c", sa.Text, comment="Revoke access from expired sessions"))
                op.execute("ALTER TABLE t ADD CHECK (a IN ('create user', 'create role'))")
                op.execute("DO $$ BEGIN RAISE EXCEPTION 'cannot grant admin to self'; END $$")
                op.execute("GRANT reader TO app")
                op.execute("ALTER ROLE app SET statement_timeout = '7s'")
                op.execute("REVOKE reader FROM app")
                op.execute("CREATE TABLESPACE fast LOCATION '/x'")
                op.execute("ALTER DATABASE CURRENT SET work_mem = '64MB'")
                op.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1) THEN CREATE ROLE g_idem; END IF; END $$")
            '''
        )
        found = _global_statements(self._write(tmp_path, body))
        assert [text for _, text in found] == [
            "GRANT reader TO app",
            "ALTER ROLE app",
            "REVOKE reader FROM app",
            "CREATE TABLESPACE fast",
            "ALTER DATABASE CURRENT",
            "CREATE ROLE g_idem",
        ]

    def test_pg_dump_14_matview_owner_form(self) -> None:
        assert _MATVIEW_OWNER_TO.match("ALTER TABLE public.m OWNER TO o")
        assert _MATVIEW_OWNER_TO.match("ALTER MATERIALIZED VIEW public.m OWNER TO o")

    def test_import_of_a_replaced_module(self, tmp_path: Path) -> None:
        later = _rev("r03", "r02", "from r01 import helper\nhelper()")
        project = Project(tmp_path, {"r01": _rev("r01", None), "r02": _rev("r02", "r01"), "r03": later})
        script_dir = ScriptDirectory.from_config(project.config())
        plan = _plan(script_dir, "r02")
        with pytest.raises(SquashError, match=r"r03\.py imports r01\.helper"):
            _check_before_run(script_dir, plan, project.versions / "r02_squashed.py", False)

    def test_name_collision_with_a_later_file(self, tmp_path: Path) -> None:
        project = Project(tmp_path, {"r01": _rev("r01", None), "r02_squashed": _rev("r03", "r01")})
        with pytest.raises(SquashError, match=r"r01_squashed\.py exists"):
            project.write("r01_squashed", _rev("r04", "r03"))
            squash(project.config(), "r01", server_url="postgresql://unused", pg_dump="/nonexistent/pg_dump")


class TestHelpers:
    def test_libpq_uri_has_no_password(self) -> None:
        url = sa.make_url("postgresql+psycopg://user:secret@db.example:5433/postgres?sslmode=require")
        assert _libpq_uri(url) == "postgresql://user@db.example:5433/postgres?sslmode=require"

    def test_missing_pg_dump(self, tmp_path: Path) -> None:
        project = Project(tmp_path, {"r01": _rev("r01", None)})
        with pytest.raises(SquashError, match="Cannot run /nonexistent/pg_dump"):
            squash(project.config(), "r01", server_url="postgresql://unused", pg_dump="/nonexistent/pg_dump")

    def test_main_reports_a_failed_check(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        project = Project(tmp_path, {"r01": _rev("r01", None)})
        status = main(["-c", str(project.ini), "--target", "nope", "--server-url", "postgresql://unused"])
        assert status == 1
        assert "squash failed: Cannot find revision 'nope'" in capsys.readouterr().err

    def test_main_reports_a_refused_connection(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        project = Project(tmp_path, {"r01": _rev("r01", None)})
        refused = "postgresql+psycopg://nobody@127.0.0.1:9/postgres?connect_timeout=2"
        argv = ["-c", str(project.ini), "--target", "r01", "--server-url", refused, "--pg-dump", sys.executable]
        assert main(argv) == 1
        assert "squash failed: Cannot connect to the server" in capsys.readouterr().err

    def test_write_failure_changes_no_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        old = [tmp_path / "r01.py", tmp_path / "r02.py"]
        for path in old:
            path.write_text("old")

        def fail(*_args: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(SquashError, match="disk full. No file changed"):
            _write(tmp_path / "r02_squashed.py", "new", old)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["r01.py", "r02.py"]

    def test_write_names_files_it_cannot_delete(self, tmp_path: Path) -> None:
        missing = tmp_path / "r01.py"
        with pytest.raises(SquashError, match=r"Wrote .*r02_squashed\.py, but cannot delete .*r01\.py"):
            _write(tmp_path / "r02_squashed.py", "new", [missing])
        assert (tmp_path / "r02_squashed.py").read_text() == "new"

    def test_config_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALEMBIC_CONFIG", raising=False)
        assert _config_files([]) == ("alembic.ini", "pyproject.toml")
        assert _config_files(["a/pyproject.toml", "b.ini"]) == ("b.ini", "a/pyproject.toml")
        monkeypatch.setenv("ALEMBIC_CONFIG", "x/pyproject.toml")
        assert _config_files([]) == ("alembic.ini", "x/pyproject.toml")
        monkeypatch.setenv("ALEMBIC_CONFIG", "custom.ini")
        assert _config_files([]) == ("custom.ini", "pyproject.toml")
        with pytest.raises(SquashError, match="at most one ini file"):
            _config_files(["a.ini", "b.ini"])

    def test_write_replaces_then_deletes(self, tmp_path: Path) -> None:
        old = [tmp_path / "r01.py", tmp_path / "r02.py"]
        for path in old:
            path.write_text("old")
        _write(tmp_path / "r02.py", "new", old)
        assert [(p.name, p.read_text()) for p in tmp_path.iterdir()] == [("r02.py", "new")]


@pytest.mark.integration
@needs_pg_dump
class TestSquash:
    def test_squash_replaces_the_chain(self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL]) -> None:
        project = Project(tmp_path, CHAIN)
        deployed = make_db()
        command.upgrade(project.config(deployed), "r07")

        result = squash(project.config(), "r07", server_url=pg_engine.url, allow_data_loss=True)

        assert project.files() == {"r07_squashed.py", "r08.py", "r09.py"}
        assert result.revision_path == project.versions / "r07_squashed.py"
        assert [p.name for p in result.removed_paths] == [f"r0{i}.py" for i in range(1, 8)]
        assert result.statement_count > 0
        assert result.server_version
        assert result.pg_dump_version.startswith("pg_dump")
        assert "table public.account: 1 rows with the old chain, 0 rows with the new" in result.warnings
        assert (
            "sequence public.account_id_seq: last_value 1 is_called True with the old chain, "
            "last_value 1 is_called False with the new" in result.warnings
        )
        assert _leftovers(pg_engine) == []

        squashed = ScriptDirectory.from_config(project.config()).get_revision("r07")
        assert squashed.down_revision is None
        assert squashed.module.squashed_revisions == tuple(f"r0{i}" for i in range(1, 8))
        assert "-- a comment line inside the body" in result.source
        assert "REFRESH MATERIALIZED VIEW public.post_ids" in result.source
        assert result.source.index("REFRESH MATERIALIZED VIEW public.post_ids") < result.source.index(
            "REFRESH MATERIALIZED VIEW public.post_id_count"
        )

        command.upgrade(project.config(deployed), "head")
        assert _version(deployed) == {"r09"}

        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        assert _version(fresh) == {"r09"}
        assert _scalars(fresh, "SELECT n FROM post_id_count") == {0}

    def test_lost_rows_refuse_by_default(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(tmp_path, {k: v for k, v in CHAIN.items() if k <= "r07"})

        with pytest.raises(SquashError, match=r"(?s)loses data at r07.*table public\.account: 1 rows.*allow_data_loss"):
            squash(project.config(), "r07", server_url=pg_engine.url)

        assert "r01.py" in project.files()
        assert _leftovers(pg_engine) == []

    def test_dry_run_changes_no_file(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(tmp_path, CHAIN)
        before = {p.name: p.read_text() for p in project.versions.glob("*.py")}

        result = squash(project.config(), "r07", server_url=pg_engine.url, allow_data_loss=True, dry_run=True)

        assert {p.name: p.read_text() for p in project.versions.glob("*.py")} == before
        assert "squashed_revisions = ('r01', 'r02', 'r03', 'r04', 'r05', 'r06', 'r07')" in result.source
        assert not result.revision_path.exists()

    def test_preflight_protects_the_database_that_env_py_chooses(
        self,
        tmp_path: Path,
        pg_engine: Engine,
        make_db: Callable[[], sa.URL],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        revisions = {name: CHAIN[name] for name in ("r01", "r02", "r03")}
        decoy = make_db()
        command.upgrade(Project(tmp_path / "setup", revisions).config(decoy), "r01")
        project = Project(tmp_path / "project", revisions, env=DECOY_ENV)
        monkeypatch.setenv("SQUASH_TEST_DECOY_URL", decoy.render_as_string(hide_password=False))

        for dry_run in (True, False):
            with pytest.raises(SquashError, match=r"(?s)preflight failed.*env\.py must connect to the scratch"):
                squash(project.config(), "r03", server_url=pg_engine.url, dry_run=dry_run)

        assert _version(decoy) == {"r01"}
        assert _tables(decoy) == {"account", "alembic_version"}
        assert project.files() == {"r01.py", "r02.py", "r03.py"}
        assert _leftovers(pg_engine) == []

    def test_preflight_on_an_empty_decoy(
        self,
        tmp_path: Path,
        pg_engine: Engine,
        make_db: Callable[[], sa.URL],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        decoy = make_db()
        project = Project(tmp_path, {name: CHAIN[name] for name in ("r01", "r02")}, env=DECOY_ENV)
        monkeypatch.setenv("SQUASH_TEST_DECOY_URL", decoy.render_as_string(hide_password=False))

        with pytest.raises(SquashError, match="not to the scratch database"):
            squash(project.config(), "r02", server_url=pg_engine.url)

        assert _tables(decoy) == set()

    def test_owner_of_another_role_is_replayed(
        self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL], make_role: Callable[..., str]
    ) -> None:
        owner = make_role()
        project = Project(
            tmp_path,
            {
                "r01": _rev("r01", None, 'op.execute("CREATE TABLE t (id int)")'),
                "r02": _rev("r02", "r01", f"op.execute('ALTER TABLE t OWNER TO \"{owner}\"')"),
                "r03": _rev("r03", "r02", 'op.execute("CREATE TABLE u (id int)")'),
            },
        )

        result = squash(project.config(), "r02", server_url=pg_engine.url)

        assert f"ALTER TABLE public.t OWNER TO {owner}" in result.source
        assert "OWNER TO postgres" not in result.source
        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        assert _scalars(fresh, "SELECT tableowner FROM pg_tables WHERE tablename = 't'") == {owner}

    def test_global_statements_refuse_before_any_database(self, tmp_path: Path, pg_engine: Engine) -> None:
        role = f"sq_chain_{uuid.uuid4().hex[:8]}"
        project = Project(
            tmp_path,
            {
                "r01": _rev("r01", None, f'op.execute("CREATE TABLE t (id int)")\nop.execute("CREATE ROLE {role}")'),
                "r02": _rev("r02", "r01", f'op.execute("GRANT SELECT ON t TO {role}")'),
            },
        )

        for dry_run in (True, False):
            with pytest.raises(SquashError, match=r"(?s)change objects of the whole server.*r01\.py:\d+: CREATE ROLE"):
                squash(project.config(), "r02", server_url=pg_engine.url, dry_run=dry_run)
            assert _leftovers(pg_engine) == []

        assert project.files() == {"r01.py", "r02.py"}

    def test_server_state_is_a_second_check_and_nothing_is_dropped(
        self, tmp_path: Path, pg_engine: Engine, make_role: Callable[..., str]
    ) -> None:
        role = make_role(f"sq_chain_{uuid.uuid4().hex[:8]}")
        hidden = f"op.execute(\"DO $$ BEGIN EXECUTE 'CREATE ' || 'ROLE {role}'; END $$\")"
        project = Project(tmp_path, {"r01": _rev("r01", None, hidden), "r02": _rev("r02", "r01")})

        with pytest.raises(
            SquashError, match=rf"(?s)During the replaced revisions.*\+ role {role} .*Remove them yourself"
        ):
            squash(project.config(), "r02", server_url=pg_engine.url)

        assert _leftovers(pg_engine) == [role]

    def test_global_objects_after_the_target(
        self, tmp_path: Path, pg_engine: Engine, make_role: Callable[..., str]
    ) -> None:
        role = make_role(f"sq_chain_{uuid.uuid4().hex[:8]}")
        project = Project(
            tmp_path,
            {"r01": _rev("r01", None), "r02": _rev("r02", "r01", f'op.execute("CREATE ROLE {role}")')},
        )

        with pytest.raises(SquashError, match="During the revisions after the target, objects of the whole server"):
            squash(project.config(), "r01", server_url=pg_engine.url)

        assert _leftovers(pg_engine) == [role]

    def test_inherited_column_order_is_compared(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(
            tmp_path,
            {
                "r01": _rev("r01", None, 'op.execute("CREATE TABLE parent (id int)")'),
                "r02": _rev("r02", "r01", 'op.execute("CREATE TABLE child (c int) INHERITS (parent)")'),
                "r03": _rev("r03", "r02", 'op.execute("ALTER TABLE parent ADD COLUMN x int")'),
            },
        )

        with pytest.raises(
            SquashError, match=r"(?s)different catalog properties at r03.*columns public\.child: id, c, x"
        ):
            squash(project.config(), "r03", server_url=pg_engine.url)

    def test_engine_connection_has_no_open_transaction(
        self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL]
    ) -> None:
        project = Project(
            tmp_path,
            {
                "r01": _rev(
                    "r01",
                    None,
                    'op.execute("CREATE TABLE t (id int, m text)")\nop.execute("CREATE TYPE mood AS ENUM (\'a\')")',
                ),
                "r02": _rev(
                    "r02",
                    "r01",
                    """
                    with op.get_context().autocommit_block():
                        op.execute("CREATE INDEX CONCURRENTLY t_id_idx ON t (id)")
                    """,
                ),
                "r03": _rev("r03", "r02", "op.execute(\"ALTER TYPE mood ADD VALUE 'b'\")"),
                "r04": _rev("r04", "r03", "op.execute(\"CREATE TABLE u (m mood DEFAULT 'b')\")"),
            },
            attributes={"transaction_per_migration": True},
        )

        squash(project.config(), "r02", server_url=pg_engine.url)

        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        assert _version(fresh) == {"r04"}

    def test_custom_version_table(self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL]) -> None:
        project = Project(
            tmp_path,
            {name: CHAIN[name] for name in ("r01", "r02", "r03")},
            attributes={"version_table": "AlembicVer", "version_table_schema": "meta"},
        )

        result = squash(
            project.config(), "r02", server_url=pg_engine.url, version_table="AlembicVer", version_table_schema="meta"
        )

        assert "CREATE SCHEMA IF NOT EXISTS meta" in result.source
        assert "AlembicVer" not in result.source
        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        assert _version(fresh, 'meta."AlembicVer"') == {"r03"}

    def test_dependency_satisfied_head(self, tmp_path: Path, pg_engine: Engine) -> None:
        a3 = _rev("a3", "a2", 'op.execute("CREATE TABLE a3 (id int)")').replace(
            "depends_on = None", "depends_on = 'x1'"
        )
        project = Project(
            tmp_path,
            {
                "a1": _rev("a1", None, 'op.execute("CREATE TABLE a1 (id int)")'),
                "a2": _rev("a2", "a1"),
                "x1": _rev("x1", None, 'op.execute("CREATE TABLE x1 (id int)")'),
                "a3": a3,
            },
        )

        squash(project.config(), "a2", server_url=pg_engine.url)

        assert project.files() == {"a2_squashed.py", "a3.py", "x1.py"}

    def test_recursive_version_locations(self, tmp_path: Path, pg_engine: Engine) -> None:
        revisions = {f"sub/{k}": v for k, v in CHAIN.items() if k in {"r01", "r02", "r03"}}
        project = Project(tmp_path, revisions, versions="ver", recursive=True)

        squash(project.config(), "r02", server_url=pg_engine.url)

        assert project.files() == {"sub/r02_squashed.py", "sub/r03.py"}

    def test_recursive_version_locations_see_a_difference(self, tmp_path: Path, pg_engine: Engine) -> None:
        revisions = {
            "sub/r01": _rev("r01", None, 'op.execute("CREATE TABLE a (id int)")'),
            "sub/r02": _rev("r02", "r01", 'op.execute("INSERT INTO a VALUES (1)")'),
            "sub/r03": _rev(
                "r03",
                "r02",
                'op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM a) THEN CREATE TABLE b (id int); END IF; END $$")',
            ),
        }
        project = Project(tmp_path, revisions, versions="ver", recursive=True)

        with pytest.raises(SquashError, match=r"(?s)different schemas at the heads.*public\.b"):
            squash(project.config(), "r02", server_url=pg_engine.url, allow_data_loss=True)

        assert project.files() == {"sub/r01.py", "sub/r02.py", "sub/r03.py"}

    def test_url_only_env_and_versions_outside_the_script_directory(
        self, tmp_path: Path, pg_engine: Engine, capsys: pytest.CaptureFixture[str]
    ) -> None:
        revisions = {name: CHAIN[name] for name in ("r01", "r02", "r03")}
        project = Project(tmp_path, revisions, env=URL_ONLY_ENV, versions="other_versions")
        url = pg_engine.url.render_as_string(hide_password=False)

        status = main(["-c", str(project.ini), "--target", "r02", "--server-url", url])

        assert status == 0
        assert "Wrote" in capsys.readouterr().out
        assert project.files() == {"r02_squashed.py", "r03.py"}

    def test_main_with_pyproject_toml_only(
        self,
        tmp_path: Path,
        pg_engine: Engine,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project = Project(tmp_path, {name: CHAIN[name] for name in ("r01", "r02")})
        project.ini.unlink()
        (tmp_path / "pyproject.toml").write_text('[tool.alembic]\nscript_location = "%(here)s/migrations"\n')
        monkeypatch.chdir(tmp_path)
        url = pg_engine.url.render_as_string(hide_password=False)

        status = main(["--target", "r02", "--server-url", url, "--dry-run"])

        assert status == 0
        assert "squashed_revisions = ('r01', 'r02')" in capsys.readouterr().out
        assert project.files() == {"r01.py", "r02.py"}

    def test_newer_pg_dump_setting_unknown_to_the_server(
        self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL]
    ) -> None:
        with pg_engine.connect() as conn:
            known = set(conn.execute(sa.text("SELECT name FROM pg_settings")).scalars())
        if "transaction_timeout" in known:
            pytest.skip("the server knows transaction_timeout")
        wrapper = tmp_path / "pg_dump"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'"{shutil.which("pg_dump")}" "$@" | sed "s/^SET lock_timeout = 0;$/&\\nSET transaction_timeout = 0;/"\n'
        )
        wrapper.chmod(0o755)
        project = Project(tmp_path / "project", {name: CHAIN[name] for name in ("r01", "r02", "r03")})

        result = squash(project.config(), "r02", server_url=pg_engine.url, pg_dump=str(wrapper))

        assert "transaction_timeout" not in result.source
        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        assert _version(fresh) == {"r03"}

    def test_env_that_reads_the_url_from_the_config(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(tmp_path, {name: CHAIN[name] for name in ("r01", "r02")}, env=URL_ONLY_ENV)
        result = squash(project.config(), "r02", server_url=pg_engine.url, dry_run=True)
        assert result.statement_count > 0

    def test_different_schema_at_the_heads(self, tmp_path: Path, pg_engine: Engine) -> None:
        seeded_index = _rev(
            "r08",
            "r07",
            """
            op.execute(
                "DO $$ BEGIN IF EXISTS (SELECT 1 FROM account) THEN "
                "CREATE INDEX account_seeded_idx ON account (email); END IF; END $$"
            )
            """,
        )
        project = Project(tmp_path, {**{k: v for k, v in CHAIN.items() if k <= "r07"}, "r08": seeded_index})

        with pytest.raises(SquashError, match=r"(?s)different schemas at the heads.*account_seeded_idx"):
            squash(project.config(), "r07", server_url=pg_engine.url, allow_data_loss=True)

        assert "r01.py" in project.files()

    def test_pg_dump_failure(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(tmp_path, {name: CHAIN[name] for name in ("r01", "r02")})

        with pytest.raises(SquashError, match=r"pg_dump failed with status"):
            squash(project.config(), "r02", server_url=pg_engine.url, pg_dump_args=["--no-such-option"])

        assert project.files() == {"r01.py", "r02.py"}
        assert _leftovers(pg_engine) == []

    def test_preflight_with_an_env_py_that_catches_errors(
        self,
        tmp_path: Path,
        pg_engine: Engine,
        make_db: Callable[[], sa.URL],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        revisions = {name: CHAIN[name] for name in ("r01", "r02", "r03")}
        decoy = make_db()
        command.upgrade(Project(tmp_path / "setup", revisions).config(decoy), "r01")
        project = Project(tmp_path / "project", revisions, env=SWALLOW_ENV)
        monkeypatch.setenv("SQUASH_TEST_DECOY_URL", decoy.render_as_string(hide_password=False))

        with pytest.raises(SquashError, match=r"env\.py also connected to \['squash_test_\w+'\]"):
            squash(project.config(), "r03", server_url=pg_engine.url, dry_run=True)

        assert _version(decoy) == {"r01"}
        assert _tables(decoy) == {"account", "alembic_version"}

    def test_sequence_restart_is_compared(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(
            tmp_path,
            {
                "r01": _rev("r01", None, 'op.execute("CREATE SEQUENCE s")'),
                "r02": _rev("r02", "r01", 'op.execute("ALTER SEQUENCE s RESTART WITH 1000")'),
            },
        )

        with pytest.raises(SquashError, match=r"sequence public\.s: last_value 1000 is_called False with the old"):
            squash(project.config(), "r02", server_url=pg_engine.url)

    def test_extra_sql_is_checked_and_carried_forward(
        self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL]
    ) -> None:
        reads_seed = (
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM kind) THEN RAISE EXCEPTION 'seed missing'; END IF; END $$"
        )
        project = Project(
            tmp_path,
            {
                "r01": _rev("r01", None, 'op.execute("CREATE TABLE kind (code text PRIMARY KEY)")'),
                "r02": _rev("r02", "r01", "op.execute(\"INSERT INTO kind VALUES ('a'), ('b')\")"),
                "r03": _rev("r03", "r02", f'op.execute("{reads_seed}")'),
                "r04": _rev("r04", "r03", 'op.execute("CREATE TABLE later (id int)")'),
            },
        )

        with pytest.raises(SquashError, match="seed missing"):
            squash(project.config(), "r02", server_url=pg_engine.url, allow_data_loss=True)

        result = squash(
            project.config(), "r02", server_url=pg_engine.url, extra_sql="INSERT INTO kind VALUES ('a'), ('b');"
        )
        assert "EXTRA_STATEMENTS = (\n    \"INSERT INTO kind VALUES ('a'), ('b')\",\n)" in result.source

        again = squash(project.config(), "r03", server_url=pg_engine.url)
        assert "INSERT INTO kind VALUES ('a'), ('b')" in again.source
        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        assert _scalars(fresh, "SELECT code FROM kind") == {"a", "b"}

    def test_view_rule_cycle_and_populated_matview(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(
            tmp_path,
            {
                "r01": _rev(
                    "r01",
                    None,
                    """
                    op.execute("CREATE TABLE t (id int)")
                    op.execute("CREATE VIEW v1 AS SELECT id FROM t")
                    op.execute("CREATE VIEW v2 AS SELECT id FROM v1")
                    op.execute("CREATE RULE r AS ON INSERT TO v1 DO INSTEAD INSERT INTO v2 VALUES (NEW.id)")
                    op.execute("CREATE MATERIALIZED VIEW mv AS SELECT id FROM v2")
                    """,
                ),
                "r02": _rev("r02", "r01"),
            },
        )

        result = squash(project.config(), "r02", server_url=pg_engine.url)

        assert "REFRESH MATERIALIZED VIEW public.mv" in result.source

    def test_matview_refresh_cases(
        self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL], make_role: Callable[..., str]
    ) -> None:
        owner = make_role()
        with pg_engine.connect() as conn:
            version = int(conn.execute(sa.text("SHOW server_version_num")).scalar_one())
        # PostgreSQL 17 and newer refresh with a fixed safe search_path, so the old chain itself cannot use a function
        # with an unqualified table name there.
        by_function = (
            """
            op.execute("CREATE FUNCTION ids() RETURNS SETOF int LANGUAGE sql AS 'SELECT id FROM t'")
            op.execute("CREATE MATERIALIZED VIEW by_function AS SELECT * FROM ids() AS id")
            """
            if version < 170000
            else ""
        )
        body = (
            'op.execute("CREATE TABLE t (id int)")\n'
            + textwrap.dedent(by_function)
            + textwrap.dedent(
                f"""
                op.execute("CREATE MATERIALIZED VIEW base AS SELECT id FROM t")
                op.execute("CREATE MATERIALIZED VIEW over_base AS SELECT id FROM base")
                op.execute("REFRESH MATERIALIZED VIEW base WITH NO DATA")
                op.execute("CREATE MATERIALIZED VIEW owned AS SELECT id FROM t")
                op.execute('ALTER MATERIALIZED VIEW owned OWNER TO "{owner}"')
                """
            )
        )
        project = Project(tmp_path, {"r01": _rev("r01", None, body), "r02": _rev("r02", "r01")})

        result = squash(project.config(), "r02", server_url=pg_engine.url)

        assert "REFRESH MATERIALIZED VIEW public.base WITH NO DATA" in result.source
        # pg_dump 14 writes ALTER TABLE for a materialized view owner, newer versions write ALTER MATERIALIZED VIEW.
        owner_statement = re.search(rf"ALTER (?:MATERIALIZED VIEW|TABLE) public\.owned OWNER TO {owner}", result.source)
        assert owner_statement is not None
        assert result.source.index("REFRESH MATERIALIZED VIEW public.owned'") < owner_statement.start()
        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        populated = "SELECT relname FROM pg_class WHERE relkind = 'm' AND relispopulated"
        expected: set[str] = {"over_base", "owned"} | ({"by_function"} if version < 170000 else set())
        assert _scalars(fresh, populated) == expected

    def test_explicit_owner_is_kept_only_for_the_named_object(self, tmp_path: Path, pg_engine: Engine) -> None:
        with pg_engine.connect() as conn:
            me = str(conn.execute(sa.text("SELECT current_user")).scalar_one())
        project = Project(
            tmp_path,
            {
                "r01": _rev(
                    "r01", None, 'op.execute("CREATE TABLE t (id int)")\nop.execute("CREATE TABLE other (id int)")'
                ),
                "r02": _rev("r02", "r01", f"op.execute('ALTER TABLE t OWNER TO \"{me}\"')"),
            },
        )

        result = squash(project.config(), "r02", server_url=pg_engine.url)

        assert "ALTER TABLE public.t OWNER TO" in result.source
        assert "ALTER TABLE public.other OWNER TO" not in result.source
        assert "ALTER SCHEMA public OWNER TO" not in result.source

    def test_preflight_with_an_env_py_that_migrates_two_databases(
        self,
        tmp_path: Path,
        pg_engine: Engine,
        make_db: Callable[[], sa.URL],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        revisions = {name: CHAIN[name] for name in ("r01", "r02", "r03")}
        decoy = make_db()
        command.upgrade(Project(tmp_path / "setup", revisions).config(decoy), "r01")
        project = Project(tmp_path / "project", revisions, env=TWO_TARGET_ENV)
        monkeypatch.setenv("SQUASH_TEST_DECOY_URL", decoy.render_as_string(hide_password=False))

        with pytest.raises(SquashError, match=r"env\.py also connected to \['squash_test_\w+'\]"):
            squash(project.config(), "r03", server_url=pg_engine.url, dry_run=True)

        assert _version(decoy) == {"r01"}
        assert _tables(decoy) == {"account", "alembic_version"}

    def test_preflight_with_a_tenant_loop_in_one_database(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(tmp_path, {"r01": _rev("r01", None), "r02": _rev("r02", "r01")}, env=TENANT_LOOP_ENV)

        with pytest.raises(SquashError, match="holds versions"):
            squash(project.config(), "r02", server_url=pg_engine.url, dry_run=True)

    def test_global_statement_override(self, tmp_path: Path, pg_engine: Engine) -> None:
        label = 'LABEL = "Grant tokens to users"'
        project = Project(tmp_path, {"r01": _rev("r01", None, extra=label), "r02": _rev("r02", "r01")})

        with pytest.raises(SquashError, match=r"(?s)Grant tokens to users.*--allow-global-statements"):
            squash(project.config(), "r02", server_url=pg_engine.url)

        result = squash(project.config(), "r02", server_url=pg_engine.url, allow_global_statements=True)
        assert any("Accepted a statement" in w and "Grant tokens to users" in w for w in result.warnings)

    def test_carried_rows_that_a_later_revision_changed(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(
            tmp_path,
            {
                "r01": _rev(
                    "r01",
                    None,
                    'op.execute("CREATE TABLE kind (id int PRIMARY KEY, name text)")\n'
                    "op.execute(\"INSERT INTO kind VALUES (1, 'old')\")",
                ),
                "r02": _rev("r02", "r01", "op.execute(\"UPDATE kind SET name = 'new'\")"),
                "r03": _rev("r03", "r02"),
            },
        )
        squash(project.config(), "r01", server_url=pg_engine.url, extra_sql="INSERT INTO kind VALUES (1, 'old');")

        with pytest.raises(SquashError, match=r"rows of public\.kind: digest \w+ with the old chain"):
            squash(project.config(), "r02", server_url=pg_engine.url)

        result = squash(project.config(), "r02", server_url=pg_engine.url, allow_data_loss=True, dry_run=True)
        assert any("carries 1 EXTRA_STATEMENTS" in w and "INSERT INTO kind" in w for w in result.warnings)

    def test_squash_of_every_revision(self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL]) -> None:
        project = Project(tmp_path, CHAIN)

        squash(project.config(), "head", server_url=pg_engine.url, allow_data_loss=True)

        assert project.files() == {"r09_squashed.py"}
        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        assert _version(fresh) == {"r09"}
