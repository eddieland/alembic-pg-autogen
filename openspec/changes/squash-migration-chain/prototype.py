"""Prove the assumptions the migration squash design depends on, before writing the spec.

Each PROOF block tests one assumption. Run against a throwaway PostgreSQL 16 instance with ``pg_dump`` on ``PATH``.
The role in ``URL`` must be allowed to create databases. The script creates databases named ``squash_*`` and an Alembic
project in a temporary directory. It drops the databases when it finishes.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

import postgast
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import DBAPIError, StatementError

HOST = "/run/pgs"
PORT = "54329"
USER = "postgres"
URL = f"postgresql+psycopg://{USER}@/{{db}}?host={HOST}&port={PORT}"

admin = sa.create_engine(URL.format(db="postgres"), isolation_level="AUTOCOMMIT")
created: list[str] = []


def proof(title: str) -> None:
    """Print a banner for one proof block."""
    print(f"\n{'=' * 100}\nPROOF: {title}\n{'=' * 100}")


def fresh_db(name: str) -> str:
    """Create an empty database from ``template0`` and return its SQLAlchemy URL."""
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(sa.text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    created.append(name)
    return URL.format(db=name)


def pg_dump(db: str, *extra: str) -> str:
    """Return ``pg_dump --schema-only --no-owner`` output for one database."""
    args = ["pg_dump", "-h", HOST, "-p", PORT, "-U", USER, "--schema-only", "--no-owner", *extra, db]
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


RESTRICT_LINE = re.compile(r"^\\(un)?restrict .*$\n?", re.MULTILINE)


def strip_restrict(dump: str) -> str:
    r"""Remove the ``\restrict`` and ``\unrestrict`` psql meta-commands (random key per run)."""
    return RESTRICT_LINE.sub("", dump)


def make_project(root: Path, revisions: dict[str, str]) -> Config:
    """Create an Alembic project from the ``generic`` template and write one file per revision."""
    cfg = Config(str(root / "alembic.ini"))
    command.init(cfg, str(root / "migrations"), template="generic")
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    versions = root / "migrations" / "versions"
    for name, body in revisions.items():
        (versions / f"{name}.py").write_text(textwrap.dedent(body))
    return cfg


def at(cfg: Config, db_url: str) -> Config:
    """Return the same project pointed at another database. The generic ``env.py`` reads ``sqlalchemy.url``."""
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    return cfg


def revision(rev: str, down: str | None, upgrade: str, extra: str = "") -> str:
    """Render a minimal revision file."""
    return (
        f"import sqlalchemy as sa\nfrom alembic import op\n\nrevision = {rev!r}\ndown_revision = {down!r}\n"
        f"branch_labels = None\ndepends_on = None\n{extra}\n\ndef upgrade():\n"
        + textwrap.indent(textwrap.dedent(upgrade).strip(), "    ")
        + "\n\n\ndef downgrade():\n    raise NotImplementedError\n"
    )


SETTING = re.compile(r"^SET (\w+) = .*$|^SELECT pg_catalog\.set_config\('(\w+)'", re.DOTALL)


def body_statements(dump: str) -> list[str]:
    """Split a dump into statements, without the meta-commands, comments, and ``client_encoding``."""
    statements = []
    for raw in postgast.split(strip_restrict(dump)):
        stmt = "\n".join(line for line in raw.strip().splitlines() if not line.startswith("--")).strip()
        if stmt and not stmt.startswith("SET client_encoding"):
            statements.append(stmt)
    return statements


def changed_settings(statements: list[str]) -> list[str]:
    """Return the names of the settings that the body changes, in first-use order."""
    names: dict[str, None] = {}
    for stmt in statements:
        if m := SETTING.match(stmt):
            names[m.group(1) or m.group(2)] = None
    return list(names)


def render_squashed(target: str, removed: list[str], statements: list[str], *, restore: bool = True) -> str:
    """Render the squashed revision. It takes ``target``'s ID and has no ``down_revision``."""
    settings = changed_settings(statements) if restore else []
    body = ",\n".join(f"    {s!r}" for s in statements)
    return f'''"""Squashed {removed[0]}..{target} ({len(removed)} revisions).

Revision ID: {target}
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = {target!r}
down_revision = None
branch_labels = None
depends_on = None
squashed_revisions = {tuple(removed)!r}

SETTINGS = {tuple(settings)!r}
STATEMENTS = (
{body},
)


def upgrade():
    for name in SETTINGS:
        op.execute(sa.DDL(f"SELECT set_config('alembic_pg_autogen.squash_{{name}}', current_setting('{{name}}'), false)"))
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement.replace("%", "%%")))
    for name in SETTINGS:
        op.execute(sa.DDL(f"SELECT set_config('{{name}}', current_setting('alembic_pg_autogen.squash_{{name}}'), false)"))


def downgrade():
    raise NotImplementedError("{target} squashes {len(removed)} revisions and cannot be downgraded")
'''


OLD_CHAIN = {
    "r01": revision(
        "r01",
        None,
        """
        op.execute("CREATE EXTENSION citext")
        op.execute("CREATE TABLE account (id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY, email citext UNIQUE)")
        """,
    ),
    "r02": revision(
        "r02",
        "r01",
        """
        op.execute(\"\"\"
            CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN NEW.updated_at = now(); RETURN NEW; END $$
        \"\"\")
        op.execute("CREATE TABLE post (id serial PRIMARY KEY, account_id int REFERENCES account, updated_at timestamptz)")
        op.execute("CREATE TRIGGER post_touch BEFORE UPDATE ON post FOR EACH ROW EXECUTE FUNCTION touch()")
        """,
    ),
    "r03": revision(
        "r03",
        "r02",
        """
        op.add_column("account", sa.Column("legacy", sa.Integer))
        op.add_column("account", sa.Column("name", sa.Text))
        op.drop_column("account", "legacy")
        op.create_index("account_name_idx", "account", ["name"], postgresql_where=sa.text("name IS NOT NULL"))
        """,
    ),
    "r04": revision(
        "r04",
        "r03",
        """
        op.execute("CREATE FUNCTION post_count() RETURNS bigint LANGUAGE sql AS 'SELECT count(*) FROM public.post'")
        op.execute("CREATE VIEW stats AS SELECT post_count() AS posts")
        """,
    ),
    "r05": revision(
        "r05",
        "r04",
        """
        op.execute("CREATE TYPE mood AS ENUM ('sad', 'ok')")
        op.execute("ALTER TYPE mood ADD VALUE 'happy'")
        op.add_column("post", sa.Column("mood", sa.Text))
        """,
    ),
    "r06": revision(
        "r06",
        "r05",
        """
        op.rename_table("post", "article")
        op.execute("INSERT INTO account (email) VALUES ('seed@example.com')")
        """,
    ),
    "r07": revision(
        "r07",
        "r06",
        r"""
        op.execute(sa.text(r"ALTER TABLE account ADD CONSTRAINT name_shape CHECK (name <> 'a \:b' AND name NOT LIKE '%\\%')"))
        op.execute(sa.text(r"COMMENT ON TABLE account IS E'back\\slash'"))
        """,
    ),
    "r08": revision("r08", "r07", 'op.create_table("audit", sa.Column("id", sa.Integer, primary_key=True))'),
    "r09": revision("r09", "r08", 'op.create_index("article_account_idx", "article", ["account_id"])'),
}
TARGET = "r07"
REMOVED = ["r01", "r02", "r03", "r04", "r05", "r06", "r07"]

work = Path(tempfile.mkdtemp(prefix="squash_proto_"))
old = make_project(work / "old", OLD_CHAIN)

try:
    # ------------------------------------------------------------------
    proof("1. pg_dump --schema-only of a database at TARGET: preamble, meta-commands, alembic_version, order")
    src_url = fresh_db("squash_src")
    command.upgrade(at(old, src_url), TARGET)
    dump = pg_dump("squash_src")
    for line in dump.splitlines():
        if line.startswith(("SET ", "SELECT pg_catalog.set_config", "\\")):
            print("  ", line)
    print("  CREATE TABLE public.alembic_version in dump:", "CREATE TABLE public.alembic_version" in dump)
    eng = sa.create_engine(src_url)
    with eng.connect() as conn:
        members = conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_depend d JOIN pg_extension e ON d.refobjid = e.oid "
                "WHERE d.deptype = 'e' AND e.extname = 'citext'"
            )
        ).scalar()
        seed = conn.execute(sa.text("SELECT count(*) FROM account")).scalar()
    eng.dispose()
    print(
        f"  citext member objects in catalog: {members}; 'citext(' functions in dump:",
        dump.count("FUNCTION public.citext"),
    )
    print(f"  seed rows in source: {seed}; INSERT in dump:", "INSERT INTO" in dump)
    print(
        "  post_count() precedes CREATE TABLE public.article:",
        dump.index("FUNCTION public.post_count") < dump.index("CREATE TABLE public.article"),
    )
    print("  ALTER TYPE ... ADD VALUE in dump:", "ADD VALUE" in dump, "| enum as one CREATE TYPE:", "'sad'," in dump)
    print("  dropped column 'legacy' in dump:", "legacy" in dump)

    # ------------------------------------------------------------------
    proof("2. The \\restrict key changes on every run; without those lines two dumps are identical")
    second = pg_dump("squash_src")
    print("  raw dumps equal:", dump == second)
    print("  stripped dumps equal:", strip_restrict(dump) == strip_restrict(second))
    try:
        postgast.split(dump)
        print("  postgast.split accepted the raw dump")
    except Exception as e:  # noqa: BLE001
        print(f"  postgast.split on the raw dump: {type(e).__name__}: {str(e).splitlines()[0]}")
    print("  postgast.split on the stripped dump:", len(postgast.split(strip_restrict(dump))), "statements")

    # ------------------------------------------------------------------
    proof("3. op.execute(str) reads ' :b' as a bind parameter; sa.DDL with doubled % runs the dump text unchanged")
    stmt = next(s for s in body_statements(dump) if "name_shape" in s)
    print("  ", stmt.replace("\n", "\n   "))
    probe_url = fresh_db("squash_probe")
    eng = sa.create_engine(probe_url)
    with eng.connect() as conn:
        conn.execute(sa.text("CREATE EXTENSION citext"))
        sp = conn.begin_nested()
        try:
            conn.execute(sa.text(stmt))
            print("  UNEXPECTED: text() accepted the statement")
        except StatementError as e:
            print(f"  sa.text(): {type(e).__name__}: {str(e).splitlines()[0]}")
        sp.rollback()
        conn.execute(sa.DDL(stmt.replace("%", "%%")))
        back = conn.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'name_shape'"))
        print("  sa.DDL(): constraint reads back as", back.scalar())
        conn.rollback()
    eng.dispose()

    # ------------------------------------------------------------------
    proof("4. A body that still holds alembic_version fails under Alembic; --exclude-table removes table and key")
    full = make_project(work / "with_version", {"r07": render_squashed(TARGET, REMOVED, body_statements(dump))})
    try:
        command.upgrade(at(full, fresh_db("squash_dup")), "head")
        print("  UNEXPECTED: upgrade succeeded")
    except DBAPIError as e:
        print(f"  {type(e.orig).__name__}: {str(e.orig).splitlines()[0]}")
    body_dump = pg_dump("squash_src", "--exclude-table=public.alembic_version")
    print("  alembic_version mentions after --exclude-table:", body_dump.count("alembic_version"))
    statements = body_statements(body_dump)
    print("  body statements:", len(statements), "| settings it changes:", changed_settings(statements))

    # ------------------------------------------------------------------
    proof("5. pg_dump sets search_path to '' for the session; Alembic's own version UPDATE then fails")
    leaky = make_project(work / "leaky", {"r07": render_squashed(TARGET, REMOVED, statements, restore=False)})
    for name in ("r08", "r09"):
        (work / "leaky" / "migrations" / "versions" / f"{name}.py").write_text(OLD_CHAIN[name])
    try:
        command.upgrade(at(leaky, fresh_db("squash_leak")), "head")
        print("  UNEXPECTED: upgrade succeeded")
    except DBAPIError as e:
        print(f"  without restore: {type(e.orig).__name__}: {str(e.orig).splitlines()[0]}")

    new_revisions = {"r07": render_squashed(TARGET, REMOVED, statements)}
    new_revisions.update({name: OLD_CHAIN[name] for name in ("r08", "r09")})
    new = make_project(work / "new", new_revisions)
    command.upgrade(at(new, fresh_db("squash_new_head")), "head")
    print("  with restore: fresh database reached head")

    # ------------------------------------------------------------------
    proof("6. Old chain at TARGET and squashed revision give identical dumps, at TARGET and at head")
    command.upgrade(at(new, fresh_db("squash_new_target")), TARGET)
    a, b = strip_restrict(pg_dump("squash_src")), strip_restrict(pg_dump("squash_new_target"))
    print("  at TARGET equal:", a == b)
    command.upgrade(at(old, fresh_db("squash_old_head")), "head")
    a, b = strip_restrict(pg_dump("squash_old_head")), strip_restrict(pg_dump("squash_new_head"))
    print("  at head equal:", a == b)

    # ------------------------------------------------------------------
    proof("7. The squashed revision keeps TARGET's ID: existing databases need no stamp")
    command.upgrade(at(new, src_url), "head")
    print("  database at r07 (built by the old chain) upgraded to head with the new chain")
    command.upgrade(at(new, URL.format(db="squash_old_head")), "head")
    print("  database at r09 (built by the old chain): upgrade head is a no-op")
    behind = fresh_db("squash_behind")
    command.upgrade(at(old, behind), "r03")
    try:
        command.upgrade(at(new, behind), "head")
        print("  UNEXPECTED: database at r03 upgraded")
    except Exception as e:  # noqa: BLE001
        print(f"  database at r03: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    proof("8. A revision after TARGET that names a removed revision breaks the new chain; the revision map finds it")
    side = OLD_CHAIN["r09"].replace("'r09'", "'r09b'").replace("'r08'", "'r05'")
    (work / "new" / "migrations" / "versions" / "r09b.py").write_text(side)
    try:
        ScriptDirectory.from_config(new).revision_map.get_revision("head")
    except Exception as e:  # noqa: BLE001
        print(f"  new chain: {type(e).__name__}: {e}")
    (work / "new" / "migrations" / "versions" / "r09b.py").unlink()
    (work / "old" / "migrations" / "versions" / "r09b.py").write_text(side)
    old_map = ScriptDirectory.from_config(old).revision_map
    ancestors = {r.revision for r in old_map.iterate_revisions(TARGET, "base")}
    for script in old_map.iterate_revisions("heads", "base"):
        if script.revision in ancestors:
            continue
        refs = set(script._all_down_revisions) | set(script._resolved_dependencies)
        if bad := sorted(refs & (ancestors - {TARGET})):
            print(f"  check on the old chain: {script.revision} names removed revision(s) {bad}")
    (work / "old" / "migrations" / "versions" / "r09b.py").unlink()

    # ------------------------------------------------------------------
    proof("9. A module-level marker is readable through ScriptDirectory, so a guard can explain a stale database")
    marker = ScriptDirectory.from_config(new).get_revision(TARGET).module.squashed_revisions
    print("  squashed_revisions =", marker)
    print("  a database at r03 is", "older than the squash" if "r03" in marker else "unknown")

    # ------------------------------------------------------------------
    proof("10. A 'drop everything' downgrade that drops schema public also drops alembic_version")
    dropper = make_project(work / "dropper", {"r07": render_squashed(TARGET, REMOVED, statements)})
    path = work / "dropper" / "migrations" / "versions" / "r07.py"
    path.write_text(
        path.read_text().replace(
            "    raise NotImplementedError(",
            '    op.execute("DROP SCHEMA public CASCADE")\n    op.execute("CREATE SCHEMA public")\n    return (',
        )
    )
    drop_url = fresh_db("squash_drop")
    command.upgrade(at(dropper, drop_url), "head")
    try:
        command.downgrade(at(dropper, drop_url), "base")
        print("  UNEXPECTED: downgrade succeeded")
    except DBAPIError as e:
        print(f"  {type(e.orig).__name__}: {str(e.orig).splitlines()[0]}")

    # ------------------------------------------------------------------
    proof("11. Offline mode (--sql) renders the body with one % and the ' :b' literal intact")
    out = work / "offline.sql"
    with out.open("w") as fh:
        offline = Config(str(work / "new" / "alembic.ini"), output_buffer=fh)
        offline.set_main_option("script_location", str(work / "new" / "migrations"))
        offline.set_main_option("sqlalchemy.url", URL.format(db="squash_probe"))
        command.upgrade(offline, f"base:{TARGET}", sql=True)
    sql_text = out.read_text()
    print("  ' :b' present:", "'a :b'" in sql_text, "| '%\\\\%' present:", "'%\\\\%'" in sql_text)

    # ------------------------------------------------------------------
    proof("12. Time to build a database: a 300-revision chain against its squash")
    n = 300
    long_chain = {
        "c000": revision("c000", None, 'op.create_table("t0", sa.Column("id", sa.Integer, primary_key=True))')
    }
    for i in range(1, n):
        k = i // 3
        table = f"t{i if i < 30 else k % 30}"
        if i < 30:
            up = f'op.create_table("{table}", sa.Column("id", sa.Integer, primary_key=True))'
        elif i % 3 == 0:
            up = f'op.add_column("{table}", sa.Column("c{k}", sa.Text))'
        elif i % 3 == 1:
            up = f'op.create_index("ix{k}", "{table}", ["c{k}"])'
        else:
            up = f'op.drop_index("ix{k}", "{table}")\nop.alter_column("{table}", "c{k}", type_=sa.String(50))'
        long_chain[f"c{i:03d}"] = revision(f"c{i:03d}", f"c{i - 1:03d}", up)
    tip = f"c{n - 1:03d}"
    chain = make_project(work / "long", long_chain)
    started = time.perf_counter()
    command.upgrade(at(chain, fresh_db("squash_long_old")), "head")
    replay = time.perf_counter() - started
    squash_stmts = body_statements(pg_dump("squash_long_old", "--exclude-table=public.alembic_version"))
    squashed = make_project(work / "long_new", {tip: render_squashed(tip, sorted(long_chain), squash_stmts)})
    started = time.perf_counter()
    command.upgrade(at(squashed, fresh_db("squash_long_new")), "head")
    single = time.perf_counter() - started
    same = strip_restrict(pg_dump("squash_long_old")) == strip_restrict(pg_dump("squash_long_new"))
    print(f"  replay of {n} revisions: {replay:.2f} s; squashed revision: {single:.2f} s; dumps equal: {same}")
finally:
    with admin.connect() as conn:
        for name in created:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()
