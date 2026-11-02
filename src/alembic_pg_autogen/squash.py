r"""Replace the start of an Alembic revision chain with one revision that builds the same schema.

The caller names a revision ``TARGET``.  :func:`squash` replaces ``TARGET`` and every ancestor of ``TARGET`` with one
revision file.  The new revision keeps the ID of ``TARGET``, so a database at or after ``TARGET`` needs no ``stamp``.

The body of the new revision is the ``pg_dump --schema-only`` output of a scratch database that the old chain builds to
``TARGET``.  Before the squash changes a file, it builds the schema with both chains and compares the ``pg_dump``
output, at ``TARGET`` and at the heads.

Run it from the command line::

    python -m alembic_pg_autogen.squash --config alembic.ini --target 3f9a1c2b7d4e \
        --server-url postgresql+psycopg://postgres@localhost/postgres

The package ``__init__`` does not import this module.
"""

from __future__ import annotations

import argparse
import difflib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast

import postgast
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import Revision
from alembic.util import CommandError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import Final

    from alembic.script import Script
    from sqlalchemy.engine import URL, Engine

log = logging.getLogger(__name__)

__all__ = ["SquashError", "SquashResult", "main", "squash"]


class SquashError(Exception):
    """A check failed, so the squash changed no file."""


class SquashResult(NamedTuple):
    """The outcome of :func:`squash`.

    Attributes:
        revision_path: The path of the new revision file.  With ``dry_run``, the squash does not write this file.
        removed_paths: The paths of the replaced revision files, from base to ``TARGET``.  With ``dry_run``, the
            squash does not delete these files.
        statement_count: The number of SQL statements in the new revision.
        server_version: The ``server_version`` setting of the PostgreSQL server.
        pg_dump_version: The first line of ``pg_dump --version``.
        source: The text of the new revision file.
        warnings: One message for each replaced file that holds data statements or global objects.  The dump
            comparison does not see these.
    """

    revision_path: Path
    removed_paths: tuple[Path, ...]
    statement_count: int
    server_version: str
    pg_dump_version: str
    source: str
    warnings: tuple[str, ...]


def squash(
    config: Config,
    target: str,
    *,
    server_url: str | URL,
    version_table: str = "alembic_version",
    version_table_schema: str = "public",
    pg_dump: str = "pg_dump",
    pg_dump_args: Sequence[str] = (),
    dry_run: bool = False,
) -> SquashResult:
    """Replace ``target`` and its ancestors with one revision.

    The function creates two scratch databases on the server that ``server_url`` names, and drops them at the end.
    The role in ``server_url`` must be allowed to create databases.

    ``env.py`` must connect to the scratch database.  The function sets the ``sqlalchemy.url`` option, and it puts an
    open connection into ``config.attributes["connection"]``.  An ``env.py`` must use one of the two.

    Args:
        config: The Alembic configuration of the project.
        target: The last revision to replace.  A unique prefix or a symbolic name such as ``head`` also works.
        server_url: A SQLAlchemy URL of the PostgreSQL server.  The database part names the database to connect to
            for ``CREATE DATABASE``.
        version_table: The ``version_table`` that ``env.py`` gives to Alembic.
        version_table_schema: The ``version_table_schema`` that ``env.py`` gives to Alembic.
        pg_dump: The ``pg_dump`` binary.
        pg_dump_args: More arguments for every ``pg_dump`` call.
        dry_run: Run every check, but write and delete no file.

    Returns:
        The paths, the statement count, the versions, and the text of the new revision.

    Raises:
        SquashError: A check failed.  The function changed no file.
    """
    script_dir = ScriptDirectory.from_config(config)
    plan = _plan(script_dir, target)
    warnings = tuple(_scan_replaced_files(plan.replaced))
    for message in warnings:
        log.warning("%s", message)

    pg_dump_version = _pg_dump_version(pg_dump)
    admin = sa.create_engine(server_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    scratch = _ScratchServer(admin)
    try:
        with admin.connect() as conn:
            server_version = str(conn.execute(sa.text("SHOW server_version")).scalar_one())
        old_url = scratch.create("old")
        new_url = scratch.create("new")
        version = _VersionTable(version_table, version_table_schema)
        dumper = _Dumper(pg_dump, tuple(pg_dump_args))

        _upgrade(config, old_url, plan.target.revision, version)
        statements = clean_dump(dumper.dump(old_url, f"--exclude-table={version.qualified}"))
        source = render_revision(plan.target, [s.revision for s in plan.replaced], statements)

        with tempfile.TemporaryDirectory(prefix="alembic_pg_autogen_squash_") as tmp:
            new_config, revision_copy = _new_chain_config(config, script_dir, plan, Path(tmp))
            revision_copy.write_text(source, encoding="utf-8")
            _upgrade(new_config, new_url, plan.target.revision, version)
            _compare(dumper.dump(old_url), dumper.dump(new_url), f"at {plan.target.revision}")
            _upgrade(config, old_url, "heads", version, expected=plan.heads)
            _upgrade(new_config, new_url, "heads", version, expected=plan.heads)
            _compare(dumper.dump(old_url), dumper.dump(new_url), "at the heads")
    finally:
        scratch.drop_all()
        admin.dispose()

    revision_path = Path(plan.target.path).with_name(f"{plan.target.revision}_squashed.py")
    removed_paths = tuple(Path(s.path) for s in plan.replaced)
    if not dry_run:
        for path in removed_paths:
            path.unlink()
        revision_path.write_text(source, encoding="utf-8")
        log.info("Wrote %s and removed %d revision files", revision_path, len(removed_paths))
    return SquashResult(
        revision_path=revision_path,
        removed_paths=removed_paths,
        statement_count=len(statements),
        server_version=server_version,
        pg_dump_version=pg_dump_version,
        source=source,
        warnings=warnings,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run :func:`squash` from the command line.

    Args:
        argv: The arguments without the program name.  ``None`` reads ``sys.argv``.

    Returns:
        The exit status: 0 on success, 1 when a check failed.
    """
    parser = argparse.ArgumentParser(
        prog="python -m alembic_pg_autogen.squash",
        description="Replace a revision and its ancestors with one revision that builds the same schema.",
    )
    parser.add_argument("-c", "--config", default="alembic.ini", help="the Alembic configuration file")
    parser.add_argument("-n", "--name", default="alembic", help="the section of the configuration file")
    parser.add_argument("--target", required=True, help="the last revision to replace")
    parser.add_argument("--server-url", required=True, help="a SQLAlchemy URL of the PostgreSQL server")
    parser.add_argument("--version-table", default="alembic_version")
    parser.add_argument("--version-table-schema", default="public")
    parser.add_argument("--pg-dump", default="pg_dump", help="the pg_dump binary")
    parser.add_argument(
        "--pg-dump-arg", action="append", default=[], dest="pg_dump_args", help="one more pg_dump argument"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the new revision and change no file")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = squash(
            Config(args.config, ini_section=args.name),
            args.target,
            server_url=args.server_url,
            version_table=args.version_table,
            version_table_schema=args.version_table_schema,
            pg_dump=args.pg_dump,
            pg_dump_args=args.pg_dump_args,
            dry_run=args.dry_run,
        )
    except (SquashError, CommandError) as e:
        print(f"squash failed: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(result.source)
        print(f"Dry run: {result.revision_path} not written, {len(result.removed_paths)} files kept", file=sys.stderr)
    else:
        print(f"Wrote {result.revision_path}")
        print(f"Removed {len(result.removed_paths)} revision files")
    print(f"Server {result.server_version}, {result.pg_dump_version}", file=sys.stderr)
    return 0


def clean_dump(dump: str) -> list[str]:
    r"""Turn ``pg_dump`` output into the statements of the new revision.

    The function removes the ``\restrict`` and ``\unrestrict`` lines, splits the text with ``postgast.split()``,
    removes the comment lines before each statement, and removes ``SET client_encoding``.  The text is a Python
    ``str``, so the driver controls the encoding.

    Args:
        dump: The output of ``pg_dump --schema-only``.

    Returns:
        The statements, in dump order.
    """
    statements: list[str] = []
    for raw in postgast.split(_strip_restrict(dump)):
        statement = _strip_leading_comments(raw)
        if statement and not _CLIENT_ENCODING.match(statement):
            statements.append(statement)
    return statements


def changed_settings(statements: Sequence[str]) -> list[str]:
    """Return the names of the session settings that ``statements`` change, in first-use order.

    Args:
        statements: The output of :func:`clean_dump`.

    Returns:
        The setting names from each ``SET`` statement and each ``set_config()`` call.
    """
    names: dict[str, None] = {}
    for statement in statements:
        if match := _SETTING.match(statement):
            names[match.group(1) or match.group(2)] = None
    return list(names)


def render_revision(target: Script, replaced: Sequence[str], statements: Sequence[str]) -> str:
    """Render the text of the new revision file.

    Args:
        target: The script of ``TARGET``.  The new revision takes its ID and its branch labels.
        replaced: The IDs of the replaced revisions, from base to ``TARGET``.
        statements: The output of :func:`clean_dump`.

    Returns:
        The text of a revision file that imports nothing from this package.
    """
    labels = tuple(sorted(target.branch_labels)) or None
    body = "".join(f"    {statement!r},\n" for statement in statements)
    return _REVISION_TEMPLATE.format(
        first=replaced[0],
        target=target.revision,
        count=len(replaced),
        target_repr=repr(target.revision),
        labels=repr(labels),
        replaced=repr(tuple(replaced)),
        settings=repr(tuple(changed_settings(statements))),
        statements=body,
    )


class _Plan(NamedTuple):
    target: Script
    replaced: list[Script]
    heads: frozenset[str]


def _plan(script_dir: ScriptDirectory, target: str) -> _Plan:
    """Find the revisions to replace and check that the rest of the chain still loads without them."""
    try:
        script = cast("Script | None", script_dir.get_revision(target))
    except CommandError as e:
        raise SquashError(f"Cannot find revision {target!r}: {e}") from e
    if script is None:
        raise SquashError(f"{target!r} names no revision")

    references = {s.revision: _references(script_dir, s) for s in script_dir.walk_revisions()}
    replaced_ids = {script.revision}
    pending = [script.revision]
    while pending:
        for ref in references[pending.pop()]:
            if ref not in replaced_ids:
                replaced_ids.add(ref)
                pending.append(ref)

    removed = replaced_ids - {script.revision}
    for rev, refs in references.items():
        if rev not in replaced_ids and (bad := sorted(refs & removed)):
            raise SquashError(
                f"Revision {rev} names {', '.join(bad)}, which the squash removes. Only {script.revision} can stay "
                f"a parent of a revision after the squash."
            )

    replaced = [s for s in reversed(list(script_dir.walk_revisions())) if s.revision in replaced_ids]
    for s in replaced:
        if s.revision != script.revision and s.branch_labels:
            raise SquashError(
                f"Revision {s.revision} has branch labels {sorted(s.branch_labels)}. Only the labels of "
                f"{script.revision} carry over to the new revision."
            )
    return _Plan(script, replaced, frozenset(script_dir.get_heads()))


def _references(script_dir: ScriptDirectory, script: Script) -> set[str]:
    """Return the IDs in ``down_revision`` and ``depends_on`` of one revision."""
    refs = set(_as_tuple(script.down_revision))
    if script.dependencies:
        resolved = script_dir.revision_map.get_revisions(script.dependencies)
        refs.update(rev.revision for rev in resolved if isinstance(rev, Revision))
    return refs


def _as_tuple(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _scan_replaced_files(replaced: Sequence[Script]) -> Iterator[str]:
    """Yield one message for each replaced file whose effect the dump comparison cannot see."""
    for script in replaced:
        text = Path(script.path).read_text(encoding="utf-8")
        if _DATA_STATEMENT.search(text):
            yield (
                f"{script.path} holds a data statement. A schema-only dump keeps no rows, so add the rows that "
                f"later revisions need to the new revision by hand."
            )
        if _GLOBAL_OBJECT.search(text):
            yield f"{script.path} changes a role, a tablespace, or a database setting. pg_dump does not dump these."


class _VersionTable(NamedTuple):
    name: str
    schema: str

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


class _ScratchServer:
    """Create scratch databases on one server and drop them all at the end."""

    def __init__(self, admin: Engine) -> None:
        self._admin: Final = admin
        self._token: Final = uuid.uuid4().hex[:12]
        self._created: list[str] = []

    def create(self, suffix: str) -> URL:
        name = f"squash_{self._token}_{suffix}"
        quoted = self._admin.dialect.identifier_preparer.quote(name)
        try:
            with self._admin.connect() as conn:
                conn.execute(sa.text(f"CREATE DATABASE {quoted} TEMPLATE template0"))
        except DBAPIError as e:
            raise SquashError(f"Cannot create the scratch database {name}: {e.orig}") from e
        self._created.append(name)
        return self._admin.url.set(database=name)

    def drop_all(self) -> None:
        with self._admin.connect() as conn:
            for name in self._created:
                quoted = self._admin.dialect.identifier_preparer.quote(name)
                conn.execute(sa.text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
        self._created.clear()


class _Dumper(NamedTuple):
    binary: str
    extra: tuple[str, ...]

    def dump(self, url: URL, *args: str) -> str:
        """Return ``pg_dump --schema-only --no-owner`` output for the database that ``url`` names."""
        argv = [self.binary, "--schema-only", "--no-owner", *self.extra, *args, f"--dbname={_libpq_uri(url)}"]
        env = dict(os.environ)
        if url.password is not None:
            env["PGPASSWORD"] = str(url.password)
        try:
            done = subprocess.run(argv, check=False, capture_output=True, text=True, env=env)
        except OSError as e:
            raise SquashError(f"Cannot run {self.binary}: {e}") from e
        if done.returncode != 0:
            raise SquashError(f"{self.binary} failed with status {done.returncode}: {done.stderr.strip()}")
        return done.stdout


def _libpq_uri(url: URL) -> str:
    """Return a libpq connection URI for ``url``, without the password."""
    bare = sa.URL.create(
        "postgresql", username=url.username, host=url.host, port=url.port, database=url.database, query=url.query
    )
    return bare.render_as_string(hide_password=False)


def _pg_dump_version(binary: str) -> str:
    try:
        done = subprocess.run([binary, "--version"], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as e:
        raise SquashError(f"Cannot run {binary}: {e}") from e
    return done.stdout.strip().splitlines()[0]


def _upgrade(
    config: Config, url: URL, revision: str, version: _VersionTable, *, expected: frozenset[str] | None = None
) -> None:
    """Run ``alembic upgrade`` against one scratch database and check that ``env.py`` used that database."""
    engine = sa.create_engine(url, poolclass=NullPool)
    try:
        scratch_config = _scratch_config(config, url)
        with engine.begin() as conn:
            scratch_config.attributes["connection"] = conn
            command.upgrade(scratch_config, revision)
        found = _read_versions(engine, version)
    finally:
        engine.dispose()
    want = expected if expected is not None else frozenset({revision})
    if found != want:
        raise SquashError(
            f"After 'upgrade {revision}', the scratch database {url.database} holds versions {sorted(found)}, not "
            f"{sorted(want)}. env.py must read the sqlalchemy.url option or use config.attributes['connection'], and "
            f"version_table and version_table_schema must match the arguments of squash()."
        )


def _read_versions(engine: Engine, version: _VersionTable) -> frozenset[str]:
    quote = engine.dialect.identifier_preparer.quote
    with engine.connect() as conn:
        exists = conn.execute(sa.text("SELECT to_regclass(:name)"), {"name": f'"{version.schema}"."{version.name}"'})
        if exists.scalar() is None:
            return frozenset()
        rows = conn.execute(sa.text(f"SELECT version_num FROM {quote(version.schema)}.{quote(version.name)}"))
        return frozenset(rows.scalars())


def _scratch_config(config: Config, url: URL) -> Config:
    """Return a copy of ``config`` whose ``sqlalchemy.url`` names the scratch database."""
    copy = Config(
        config.config_file_name,
        toml_file=config.toml_file_name,
        ini_section=config.config_ini_section,
        output_buffer=config.output_buffer,
        stdout=config.stdout,
        cmd_opts=config.cmd_opts,
        config_args=config.config_args,
        attributes=dict(config.attributes),
    )
    section = config.config_ini_section
    if not copy.file_config.has_section(section):
        copy.file_config.add_section(section)
    for key, value in config.file_config.items(section, raw=True):
        copy.file_config.set(section, key, value)
    copy.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    return copy


def _new_chain_config(config: Config, script_dir: ScriptDirectory, plan: _Plan, tmp: Path) -> tuple[Config, Path]:
    """Copy the script directory into ``tmp`` without the replaced files.

    Returns:
        A configuration that loads the copy, and the path where the new revision belongs in the copy.
    """
    root = Path(script_dir.dir).resolve()
    copy_root = tmp / "script"
    shutil.copytree(root, copy_root, ignore=shutil.ignore_patterns("__pycache__"))
    mapping: dict[Path, Path] = {}

    def mapped(directory: Path) -> Path:
        directory = directory.resolve()
        if directory.is_relative_to(root):
            return copy_root / directory.relative_to(root)
        if directory not in mapping:
            mapping[directory] = tmp / f"versions_{len(mapping)}"
            shutil.copytree(directory, mapping[directory], ignore=shutil.ignore_patterns("__pycache__"))
        return mapping[directory]

    locations = [mapped(Path(location)) for location in script_dir.version_locations]
    for script in plan.replaced:
        (mapped(Path(script.path).parent) / Path(script.path).name).unlink()

    new_config = _scratch_config(config, sa.make_url("postgresql://"))
    _set_raw(new_config, "script_location", str(copy_root))
    if config.get_version_locations_list() is not None:
        _set_raw(new_config, "path_separator", "newline")
        _set_raw(new_config, "version_locations", "\n".join(str(p) for p in locations))
        prepend = config.get_prepend_sys_paths_list()
        if prepend:
            _set_raw(new_config, "prepend_sys_path", "\n".join(prepend))
    target_path = Path(plan.target.path)
    return new_config, mapped(target_path.parent) / f"{plan.target.revision}_squashed.py"


def _set_raw(config: Config, key: str, value: str) -> None:
    config.set_main_option(key, value.replace("%", "%%"))


def _compare(old: str, new: str, where: str) -> None:
    old, new = _strip_restrict(old), _strip_restrict(new)
    if old == new:
        return
    diff = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), "old chain", "new chain", n=2
    )
    lines = list(diff)
    shown = "".join(lines[:_MAX_DIFF_LINES])
    more = f"\n... {len(lines) - _MAX_DIFF_LINES} more diff lines" if len(lines) > _MAX_DIFF_LINES else ""
    raise SquashError(f"The two chains give different schemas {where}:\n{shown}{more}")


def _strip_restrict(dump: str) -> str:
    r"""Remove the ``\restrict`` and ``\unrestrict`` psql meta-commands, whose key changes on every run."""
    return _RESTRICT_LINE.sub("", dump)


def _strip_leading_comments(statement: str) -> str:
    """Remove the blank lines and ``--`` comment lines before a statement, and nothing inside it."""
    lines = statement.strip().splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    return "\n".join(lines).strip()


_MAX_DIFF_LINES: Final = 200
_RESTRICT_LINE: Final = re.compile(r"^\\(?:un)?restrict\b.*(?:\n|$)", re.MULTILINE)
_CLIENT_ENCODING: Final = re.compile(r"SET\s+client_encoding\b", re.IGNORECASE)
_SETTING: Final = re.compile(r"SET\s+(\w+)\s*(?:=|TO)|SELECT\s+pg_catalog\.set_config\('(\w+)'", re.IGNORECASE)
_DATA_STATEMENT: Final = re.compile(r"\bbulk_insert\b|\bINSERT\s+INTO\b|\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b", re.I)
_GLOBAL_OBJECT: Final = re.compile(r"\b(?:CREATE|ALTER)\s+(?:ROLE|USER|TABLESPACE)\b|\bALTER\s+DATABASE\b", re.I)

_REVISION_TEMPLATE: Final = '''\
"""Squashed {first}..{target} ({count} revisions).

The body is the pg_dump --schema-only output of a database that the replaced revisions built.

Revision ID: {target}
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = {target_repr}
down_revision = None
branch_labels = {labels}
depends_on = None
squashed_revisions = {replaced}

SETTINGS = {settings}
STATEMENTS = (
{statements})


def upgrade():
    for name in SETTINGS:
        op.execute(sa.DDL(f"SELECT set_config('alembic_pg_autogen.squash_{{name}}', current_setting('{{name}}'), false)"))
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement.replace("%", "%%")))
    for name in SETTINGS:
        op.execute(sa.DDL(f"SELECT set_config('{{name}}', current_setting('alembic_pg_autogen.squash_{{name}}'), false)"))


def downgrade():
    raise NotImplementedError("{target} squashes {count} revisions and cannot be downgraded")
'''


if __name__ == "__main__":
    raise SystemExit(main())
