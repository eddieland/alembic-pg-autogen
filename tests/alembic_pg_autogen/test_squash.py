"""Tests for ``alembic_pg_autogen.squash``.

The unit tests need no database.  The integration tests create scratch databases on the test server and need a
``pg_dump`` binary on ``PATH``.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import shutil
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
    SquashError,
    _libpq_uri,
    _plan,
    _scan_replaced_files,
    _strip_leading_comments,
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

needs_pg_dump = pytest.mark.skipif(shutil.which("pg_dump") is None, reason="pg_dump is not on PATH")

COOKBOOK_ENV = """\
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    engine = create_engine(config.get_main_option("sqlalchemy.url"), poolclass=NullPool)
    with engine.begin() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
else:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
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

engine = create_engine(os.environ["SQUASH_TEST_DECOY_URL"], poolclass=NullPool)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
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
        op.execute("INSERT INTO account (email) VALUES ('seed@example.com')")
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
        versions_outside: bool = False,
    ) -> None:
        """Write ``env.py``, ``alembic.ini``, and the revision files under ``root``."""
        self.root: Final = root
        self.script_dir: Final = root / "migrations"
        self.versions: Final = root / "other_versions" if versions_outside else self.script_dir / "versions"
        (self.script_dir / "versions").mkdir(parents=True)
        self.versions.mkdir(exist_ok=True)
        (self.script_dir / "env.py").write_text(env)
        (self.script_dir / "script.py.mako").write_text("")
        self.ini: Final = root / "alembic.ini"
        lines = ["[alembic]", "script_location = %(here)s/migrations", "path_separator = os"]
        if versions_outside:
            lines.append("version_locations = %(here)s/other_versions")
        self.ini.write_text("\n".join(lines) + "\n")
        for name, body in revisions.items():
            self.write(name, body)

    def write(self, name: str, body: str) -> Path:
        path = self.versions / f"{name}.py"
        path.write_text(body)
        return path

    def config(self, url: sa.URL | None = None) -> Config:
        cfg = Config(str(self.ini))
        if url is not None:
            cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
        return cfg

    def files(self) -> set[str]:
        return {p.name for p in self.versions.glob("*.py")}


@pytest.fixture
def make_db(pg_engine: Engine) -> Generator[Callable[[], sa.URL]]:
    """Create empty databases on the test server and drop them after the test."""
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


def _version(url: sa.URL) -> set[str]:
    engine = sa.create_engine(url, poolclass=NullPool)
    with engine.connect() as conn:
        found = set(conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalars())
    engine.dispose()
    return found


def _scratch_databases(pg_engine: Engine) -> list[str]:
    with pg_engine.connect() as conn:
        rows = conn.execute(sa.text(r"SELECT datname FROM pg_database WHERE datname LIKE 'squash\_%\_old'"))
        return list(rows.scalars())


class TestCleanDump:
    def test_removes_meta_commands_comments_and_client_encoding(self) -> None:
        dump = textwrap.dedent(
            """\
            --
            -- PostgreSQL database dump
            --

            \\restrict abc123

            SET statement_timeout = 0;
            SET client_encoding = 'UTF8';
            SELECT pg_catalog.set_config('search_path', '', false);

            --
            -- Name: f(); Type: FUNCTION; Schema: public; Owner: -
            --

            CREATE FUNCTION public.f() RETURNS integer
                LANGUAGE plpgsql
                AS $$
            BEGIN
            -- kept, because it is inside the body
            RETURN 1;
            END $$;

            \\unrestrict abc123
            """
        )
        statements = clean_dump(dump)
        assert statements[0] == "SET statement_timeout = 0"
        assert statements[1] == "SELECT pg_catalog.set_config('search_path', '', false)"
        assert statements[2].startswith("CREATE FUNCTION public.f()")
        assert "-- kept, because it is inside the body" in statements[2]
        assert len(statements) == 3

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
        project = Project(tmp_path, {"r01": _rev("r01", None), "r02": _rev("r02", "r01", extra="")})
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
        assert "alembic_pg_autogen" not in source.split('"""', 2)[2].replace("alembic_pg_autogen.squash_", "")
        with pytest.raises(NotImplementedError, match="squashes 2 revisions"):
            namespace["downgrade"]()

    def test_target_branch_labels_carry_over(self, tmp_path: Path) -> None:
        labelled = _rev("r02", "r01").replace("branch_labels = None", "branch_labels = ('core',)")
        project = Project(tmp_path, {"r01": _rev("r01", None), "r02": labelled})
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

    def test_branch_label_on_a_removed_revision(self, tmp_path: Path) -> None:
        labelled = _rev("r01", None).replace("branch_labels = None", "branch_labels = ('core',)")
        revisions = {"r01": labelled, "r02": _rev("r02", "r01")}
        with pytest.raises(SquashError, match=r"Revision r01 has branch labels \['core'\]"):
            self._plan(tmp_path, revisions, "r02")

    def test_unknown_target(self, tmp_path: Path) -> None:
        with pytest.raises(SquashError, match="Cannot find revision 'nope'"):
            self._plan(tmp_path, {"r01": _rev("r01", None)}, "nope")

    def test_base_is_not_a_target(self, tmp_path: Path) -> None:
        with pytest.raises(SquashError, match="'base' names no revision"):
            self._plan(tmp_path, {"r01": _rev("r01", None)}, "base")


class TestHelpers:
    def test_libpq_uri_has_no_password(self) -> None:
        url = sa.make_url("postgresql+psycopg://user:secret@db.example:5433/postgres?sslmode=require")
        assert _libpq_uri(url) == "postgresql://user@db.example:5433/postgres?sslmode=require"

    def test_scan_reports_data_and_global_objects(self, tmp_path: Path) -> None:
        revisions = {
            "r01": _rev("r01", None, 'op.execute("CREATE ROLE reader")'),
            "r02": _rev("r02", "r01", 'op.bulk_insert(sa.table("t"), [])'),
            "r03": _rev("r03", "r02", 'op.execute("CREATE TABLE t (id int)")'),
        }
        script_dir = ScriptDirectory.from_config(Project(tmp_path, revisions).config())
        warnings = list(_scan_replaced_files(_plan(script_dir, "r03").replaced))
        assert len(warnings) == 2
        assert "r01.py changes a role, a tablespace, or a database setting" in warnings[0]
        assert "r02.py holds a data statement" in warnings[1]

    def test_missing_pg_dump(self, tmp_path: Path) -> None:
        project = Project(tmp_path, {"r01": _rev("r01", None)})
        with pytest.raises(SquashError, match="Cannot run /nonexistent/pg_dump"):
            squash(project.config(), "r01", server_url="postgresql://unused", pg_dump="/nonexistent/pg_dump")

    def test_main_reports_a_failed_check(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        project = Project(tmp_path, {"r01": _rev("r01", None)})
        status = main(["-c", str(project.ini), "--target", "nope", "--server-url", "postgresql://unused"])
        assert status == 1
        assert "squash failed: Cannot find revision 'nope'" in capsys.readouterr().err


@pytest.mark.integration
@needs_pg_dump
class TestSquash:
    def test_squash_replaces_the_chain(self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL]) -> None:
        project = Project(tmp_path, CHAIN)
        deployed = make_db()
        command.upgrade(project.config(deployed), "r07")

        result = squash(project.config(), "r07", server_url=pg_engine.url)

        assert project.files() == {"r07_squashed.py", "r08.py", "r09.py"}
        assert result.revision_path == project.versions / "r07_squashed.py"
        assert [p.name for p in result.removed_paths] == [f"r0{i}.py" for i in range(1, 8)]
        assert result.statement_count > 0
        assert result.server_version
        assert result.pg_dump_version.startswith("pg_dump")
        assert any("r06.py holds a data statement" in w for w in result.warnings)
        assert _scratch_databases(pg_engine) == []

        script_dir = ScriptDirectory.from_config(project.config())
        squashed = script_dir.get_revision("r07")
        assert squashed.down_revision is None
        assert squashed.module.squashed_revisions == tuple(f"r0{i}" for i in range(1, 8))
        assert "-- a comment line inside the body" in result.source

        command.upgrade(project.config(deployed), "head")
        assert _version(deployed) == {"r09"}

        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        assert _version(fresh) == {"r09"}

    def test_dry_run_changes_no_file(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(tmp_path, CHAIN)
        before = {p.name: p.read_text() for p in project.versions.glob("*.py")}

        result = squash(project.config(), "r07", server_url=pg_engine.url, dry_run=True)

        assert {p.name: p.read_text() for p in project.versions.glob("*.py")} == before
        assert "squashed_revisions = ('r01', 'r02', 'r03', 'r04', 'r05', 'r06', 'r07')" in result.source
        assert not result.revision_path.exists()

    def test_url_only_env_and_versions_outside_the_script_directory(
        self, tmp_path: Path, pg_engine: Engine, capsys: pytest.CaptureFixture[str]
    ) -> None:
        revisions = {name: CHAIN[name] for name in ("r01", "r02", "r03")}
        project = Project(tmp_path, revisions, env=URL_ONLY_ENV, versions_outside=True)
        url = pg_engine.url.render_as_string(hide_password=False)

        status = main(["-c", str(project.ini), "--target", "r02", "--server-url", url])

        assert status == 0
        assert "Wrote" in capsys.readouterr().out
        assert project.files() == {"r02_squashed.py", "r03.py"}

    def test_main_dry_run_prints_the_revision(
        self, tmp_path: Path, pg_engine: Engine, capsys: pytest.CaptureFixture[str]
    ) -> None:
        project = Project(tmp_path, {name: CHAIN[name] for name in ("r01", "r02")})
        url = pg_engine.url.render_as_string(hide_password=False)

        status = main(["-c", str(project.ini), "--target", "r02", "--server-url", url, "--dry-run"])

        assert status == 0
        assert "squashed_revisions = ('r01', 'r02')" in capsys.readouterr().out
        assert project.files() == {"r01.py", "r02.py"}

    def test_env_that_ignores_the_scratch_database(
        self,
        tmp_path: Path,
        pg_engine: Engine,
        make_db: Callable[[], sa.URL],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project = Project(tmp_path, {name: CHAIN[name] for name in ("r01", "r02")}, env=DECOY_ENV)
        decoy = make_db()
        monkeypatch.setenv("SQUASH_TEST_DECOY_URL", decoy.render_as_string(hide_password=False))

        with pytest.raises(SquashError, match=r"env\.py must read the sqlalchemy\.url option"):
            squash(project.config(), "r02", server_url=pg_engine.url)

        assert project.files() == {"r01.py", "r02.py"}
        assert _scratch_databases(pg_engine) == []

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
            squash(project.config(), "r07", server_url=pg_engine.url)

        assert "r01.py" in project.files()

    def test_pg_dump_failure(self, tmp_path: Path, pg_engine: Engine) -> None:
        project = Project(tmp_path, {name: CHAIN[name] for name in ("r01", "r02")})

        with pytest.raises(SquashError, match=r"pg_dump failed with status"):
            squash(project.config(), "r02", server_url=pg_engine.url, pg_dump_args=["--no-such-option"])

        assert project.files() == {"r01.py", "r02.py"}
        assert _scratch_databases(pg_engine) == []

    def test_squash_of_every_revision(self, tmp_path: Path, pg_engine: Engine, make_db: Callable[[], sa.URL]) -> None:
        project = Project(tmp_path, CHAIN)

        squash(project.config(), "head", server_url=pg_engine.url)

        assert project.files() == {"r09_squashed.py"}
        fresh = make_db()
        command.upgrade(project.config(fresh), "head")
        assert _version(fresh) == {"r09"}
