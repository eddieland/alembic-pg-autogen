r"""Replace the start of an Alembic revision chain with one revision that builds the same schema.

The caller names a revision ``TARGET``.  :func:`squash` replaces ``TARGET`` and every ancestor of ``TARGET`` with one
revision file.  The new revision keeps the ID of ``TARGET``, so a database at or after ``TARGET`` needs no ``stamp``.

The body of the new revision is the ``pg_dump --schema-only`` output of a scratch database that the old chain builds to
``TARGET``.  Before the squash changes a file, it builds the schema with both chains and compares them at ``TARGET``
and at the heads.  :func:`squash` lists the properties that the comparison checks.

Run it from the command line::

    python -m alembic_pg_autogen.squash --config alembic.ini --target 3f9a1c2b7d4e \
        --server-url postgresql+psycopg://postgres@localhost/postgres

The package ``__init__`` does not import this module.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import graphlib
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
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from collections.abc import Set as AbstractSet
    from typing import Final

    from alembic.script import Script
    from sqlalchemy.engine import URL, Engine

log = logging.getLogger(__name__)

__all__ = ["SquashError", "SquashResult", "main", "squash"]


class SquashError(Exception):
    """A check failed.  Unless the message says otherwise, the squash changed no file."""


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
        warnings: One message for each table or sequence whose data differs at ``TARGET``.  Only
            ``allow_data_loss=True`` turns these differences into warnings.
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
    extra_sql: str | None = None,
    allow_data_loss: bool = False,
    allow_global_statements: bool = False,
    dry_run: bool = False,
) -> SquashResult:
    """Replace ``target`` and its ancestors with one revision.

    The function creates scratch databases on the server that ``server_url`` names, and drops them at the end.  The
    role in ``server_url`` must be allowed to create databases.

    The squash refuses a chain whose replaced revisions change objects of the whole server: roles, role memberships,
    tablespaces, and database or system settings.  ``pg_dump`` does not dump these.  The check scans the SQL strings of
    the replaced revisions for these statements.  ``allow_global_statements=True`` turns a match into a warning.  It also refuses a revision after
    ``TARGET`` that imports the module of a replaced revision.

    ``env.py`` must connect to the scratch database.  The function sets the ``sqlalchemy.url`` option, and it puts an
    open connection without a transaction into ``config.attributes["connection"]``.  Before the first upgrade of the
    chain, a preflight runs ``env.py`` with one sentinel revision.  The sentinel writes a marker table only when
    ``current_database()`` is the scratch database.  In any other database, it writes a trip file before it raises.
    The squash stops unless the marker exists and the trip file does not.

    The squash compares the two chains at ``TARGET`` and at the heads.  The comparison checks these properties:

    - the ``pg_dump --schema-only`` text, with owners
    - the column order of each relation
    - whether each materialized view is populated
    - the rows of the Alembic version table
    - roles, role memberships, tablespaces, and database and role settings on the server

    At ``TARGET`` only, it also compares the row count of each table and ``last_value`` and ``is_called`` of each
    sequence.

    Args:
        config: The Alembic configuration of the project.
        target: The last revision to replace.  A unique prefix or a symbolic name such as ``head`` also works.
        server_url: A SQLAlchemy URL of the PostgreSQL server.  The database part names the database to connect to
            for ``CREATE DATABASE``.
        version_table: The ``version_table`` that ``env.py`` gives to Alembic.
        version_table_schema: The ``version_table_schema`` that ``env.py`` gives to Alembic.
        pg_dump: The ``pg_dump`` binary.
        pg_dump_args: More arguments for every ``pg_dump`` call.
        extra_sql: SQL that the new revision runs after the schema, such as ``INSERT`` statements for seed rows.  The
            comparison runs it too.  A later squash that replaces this revision carries the SQL forward.
        allow_data_loss: Report a data difference at ``TARGET`` as a warning, not as an error.
        allow_global_statements: Report a statement that changes an object of the whole server as a warning, not as
            an error.  The server state check still runs.
        dry_run: Run every check, but write and delete no file.

    Returns:
        The paths, the statement count, the versions, and the text of the new revision.

    Raises:
        SquashError: A check failed.
    """
    script_dir = ScriptDirectory.from_config(config)
    plan = _plan(script_dir, target)
    target_rev = plan.target.revision
    revision_path = Path(plan.target.path).with_name(f"{target_rev}_squashed.py")
    removed_paths = tuple(Path(s.path) for s in plan.replaced)
    warnings: list[str] = list(_check_before_run(script_dir, plan, revision_path, allow_global_statements))
    carried = _carried_extra(plan.replaced)
    if carried:
        warnings.append(
            f"The new revision carries {len(carried)} EXTRA_STATEMENTS of earlier squashes. Check that later "
            "revisions did not change these rows: " + "; ".join(carried)
        )
    extra = carried + _split_extra(extra_sql)
    extra_tables = _touched_tables(extra)
    owner_objects = _named_owner_objects(plan.replaced)
    pg_dump_version = _pg_dump_version(pg_dump)
    version = _VersionTable(version_table, version_table_schema)
    dumper = _Dumper(pg_dump, tuple(pg_dump_args))

    admin = sa.create_engine(server_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        server = _ScratchServer(admin)
    except DBAPIError as e:
        admin.dispose()
        raise SquashError(f"Cannot connect to the server: {e.orig}") from e
    try:
        with tempfile.TemporaryDirectory(prefix="alembic_pg_autogen_squash_") as tmp:
            _preflight(config, script_dir, server.create("pre"), Path(tmp) / "pre")
            old_url, new_url = server.create("old"), server.create("new")

            _upgrade(config, old_url, target_rev, version, expected=frozenset({target_rev}))
            server.check_globals("the replaced revisions")
            statements = clean_dump(
                dumper.dump(old_url, f"--exclude-table={version.pattern}"),
                known_settings=server.settings,
                owner=server.role,
                owner_objects=owner_objects,
                default_privileges_role=server.role,
                version_schema=version.schema,
            )
            statements, post = _post_statements(old_url, statements)
            source = render_revision(
                plan.target, [s.revision for s in plan.replaced], statements, extra=extra, post=post
            )

            copy = _ScriptCopy(config, script_dir, Path(tmp) / "new")
            for script in plan.replaced:
                copy.map(Path(script.path)).unlink()
            revision_copy = copy.map(revision_path)
            revision_copy.write_text(source, encoding="utf-8")
            _check_copy(copy.config, plan, revision_copy)

            _upgrade_new(copy.config, new_url, target_rev, version, expected=frozenset({target_rev}))
            _compare(dumper, old_url, new_url, f"at {target_rev}")
            differences = _data_differences(old_url, new_url, version, extra_tables)
            if differences and not allow_data_loss:
                raise SquashError(
                    f"The new revision loses data at {target_rev}:\n"
                    + "\n".join(f"  {d}" for d in differences)
                    + "\nPass the rows and setval() calls as extra_sql (--extra-sql FILE). Or accept the loss with "
                    "allow_data_loss=True (--allow-data-loss)."
                )
            warnings.extend(differences)

            old_heads = _upgrade(config, old_url, "heads", version)
            server.check_globals("the revisions after the target")
            new_heads = _upgrade_new(copy.config, new_url, "heads", version)
            if old_heads != new_heads:
                raise SquashError(
                    f"At the heads, the old chain holds versions {sorted(old_heads)} and the new chain holds "
                    f"{sorted(new_heads)}."
                )
            _compare(dumper, old_url, new_url, "at the heads")
    finally:
        server.close()

    for message in warnings:
        log.warning("%s", message)
    if not dry_run:
        _write(revision_path, source, removed_paths)
    return SquashResult(
        revision_path=revision_path,
        removed_paths=removed_paths,
        statement_count=len(statements) + len(extra) + len(post),
        server_version=server.version,
        pg_dump_version=pg_dump_version,
        source=source,
        warnings=tuple(warnings),
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
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        default=[],
        help="an Alembic ini file or pyproject.toml, as for the alembic command; give it twice for both",
    )
    parser.add_argument("-n", "--name", default="alembic", help="the section of the ini file")
    parser.add_argument("--target", required=True, help="the last revision to replace")
    parser.add_argument("--server-url", required=True, help="a SQLAlchemy URL of the PostgreSQL server")
    parser.add_argument("--version-table", default="alembic_version")
    parser.add_argument("--version-table-schema", default="public")
    parser.add_argument("--pg-dump", default="pg_dump", help="the pg_dump binary")
    parser.add_argument(
        "--pg-dump-arg", action="append", default=[], dest="pg_dump_args", help="one more pg_dump argument"
    )
    parser.add_argument("--extra-sql", type=Path, help="a SQL file that the new revision runs after the schema")
    parser.add_argument(
        "--allow-data-loss", action="store_true", help="report lost rows and sequence state as warnings"
    )
    parser.add_argument(
        "--allow-global-statements",
        action="store_true",
        help="report statements that change roles or tablespaces as warnings",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the new revision and change no file")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        ini, toml = _config_files(args.config)
        extra_sql = args.extra_sql.read_text(encoding="utf-8") if args.extra_sql else None
        result = squash(
            Config(ini, toml_file=toml, ini_section=args.name),
            args.target,
            server_url=args.server_url,
            version_table=args.version_table,
            version_table_schema=args.version_table_schema,
            pg_dump=args.pg_dump,
            pg_dump_args=args.pg_dump_args,
            extra_sql=extra_sql,
            allow_data_loss=args.allow_data_loss,
            allow_global_statements=args.allow_global_statements,
            dry_run=args.dry_run,
        )
    except (SquashError, CommandError, SQLAlchemyError, OSError) as e:
        print(f"squash failed: {e}", file=sys.stderr)
        return 1

    for message in result.warnings:
        print(f"warning: {message}", file=sys.stderr)
    if args.dry_run:
        print(result.source)
        print(f"Dry run: {result.revision_path} not written, {len(result.removed_paths)} files kept", file=sys.stderr)
    else:
        print(f"Wrote {result.revision_path}")
        print(f"Removed {len(result.removed_paths)} revision files")
    print(f"Server {result.server_version}, {result.pg_dump_version}", file=sys.stderr)
    return 0


def clean_dump(
    dump: str,
    *,
    known_settings: Iterable[str] | None = None,
    owner: str | None = None,
    owner_objects: AbstractSet[str] | None = None,
    default_privileges_role: str | None = None,
    version_schema: str | None = None,
) -> list[str]:
    r"""Turn ``pg_dump`` output into the statements of the new revision.

    The function removes the ``\restrict`` and ``\unrestrict`` pair, splits the text with ``postgast.split()``, and
    removes the comment lines before each statement.  It then removes these statements:

    - ``SET client_encoding``.  The text is a Python ``str``, so the driver controls the encoding.
    - ``SET`` and ``set_config()`` of a setting outside ``known_settings``.  A newer ``pg_dump`` sets
      ``transaction_timeout``, which an older server does not know.
    - ``COMMENT ON EXTENSION``.  Only the owner of the extension can run it.
    - ``ALTER ... OWNER TO`` the role ``owner``, unless the object is in ``owner_objects``.  The role that runs the
      migration owns new objects already.  An object that a replaced revision gave an owner by name keeps the
      statement.  ``ALTER SCHEMA public OWNER TO owner`` never stays.

    ``ALTER DEFAULT PRIVILEGES FOR ROLE default_privileges_role`` loses its ``FOR ROLE`` clause, so it applies to the
    role that runs the migration, as the replaced revision did.

    A ``CREATE SCHEMA`` of ``version_schema`` becomes ``CREATE SCHEMA IF NOT EXISTS``, because ``env.py`` often
    creates that schema before Alembic creates the version table.

    Args:
        dump: The output of ``pg_dump --schema-only``.
        known_settings: The setting names that the server knows.  ``None`` keeps every setting.
        owner: The role that ran the replaced revisions.
        owner_objects: The bare names of the objects that a replaced revision gave an owner by name.
        default_privileges_role: The role that ran the replaced revisions.
        version_schema: The schema of the Alembic version table.

    Returns:
        The statements, in dump order.

    Raises:
        SquashError: ``postgast`` cannot split the dump.
    """
    known = frozenset(known_settings) if known_settings is not None else None
    try:
        raw_statements = postgast.split(_strip_restrict(dump))
    except postgast.PgQueryError as e:
        raise SquashError(f"postgast cannot split the pg_dump output: {e}") from e

    statements: list[str] = []
    for raw in raw_statements:
        statement = _strip_leading_comments(raw)
        if not statement or _CLIENT_ENCODING.match(statement) or _COMMENT_ON_EXTENSION.match(statement):
            continue
        if known is not None and (setting := _setting_name(statement)) and setting not in known:
            continue
        if owner is not None and (match := _OWNER_TO.match(statement)) and _unquote(match.group(1)) == owner:
            name = _owned_object(statement)
            if name is None or name == "public" or name not in (owner_objects or ()):
                continue
        if default_privileges_role is not None and (match := _DEFAULT_PRIVILEGES_FOR_ROLE.match(statement)):
            if _unquote(match.group(2)) == default_privileges_role:
                statement = match.group(1) + statement[match.end() :]
        if version_schema is not None and (match := _CREATE_SCHEMA.match(statement)):
            if _unquote(match.group(1)) == version_schema:
                statement = f"CREATE SCHEMA IF NOT EXISTS {match.group(1)}"
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
        if name := _setting_name(statement):
            names[name] = None
    return list(names)


def render_revision(
    target: Script,
    replaced: Sequence[str],
    statements: Sequence[str],
    *,
    extra: Sequence[str] = (),
    post: Sequence[str] = (),
) -> str:
    """Render the text of the new revision file.

    Args:
        target: The script of ``TARGET``.  The new revision takes its ID and the branch labels that it declares.
        replaced: The IDs of the replaced revisions, from base to ``TARGET``.
        statements: The output of :func:`clean_dump`.  These run with the settings of the dump.
        extra: The statements of ``extra_sql``.  These run after the settings are restored.
        post: Statements that run last, such as ``REFRESH MATERIALIZED VIEW``.

    Returns:
        The text of a revision file that imports nothing from this package.
    """
    labels = _declared_labels(target) or None
    return _REVISION_TEMPLATE.format(
        first=replaced[0],
        target=target.revision,
        count=len(replaced),
        target_repr=repr(target.revision),
        labels=repr(labels),
        replaced=repr(tuple(replaced)),
        settings=repr(tuple(changed_settings(statements))),
        statements=_tuple_body(statements),
        extra=_tuple_body(extra),
        post=_tuple_body(post),
    )


def _tuple_body(statements: Sequence[str]) -> str:
    return "".join(f"    {statement!r},\n" for statement in statements)


class _Plan(NamedTuple):
    target: Script
    replaced: list[Script]


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
        if s.revision != script.revision and (labels := _declared_labels(s)):
            raise SquashError(
                f"Revision {s.revision} declares branch labels {list(labels)}. Only the labels of "
                f"{script.revision} carry over to the new revision."
            )
    return _Plan(script, replaced)


def _check_before_run(
    script_dir: ScriptDirectory, plan: _Plan, revision_path: Path, allow_global_statements: bool
) -> list[str]:
    """Refuse chains that the squash cannot handle, before it connects to the server.

    Returns:
        The warnings for global statements that ``allow_global_statements`` accepts.
    """
    replaced_paths = {Path(s.path).resolve() for s in plan.replaced}
    if revision_path.exists() and revision_path.resolve() not in replaced_paths:
        raise SquashError(f"{revision_path} exists and is not a replaced revision. Rename that file first.")

    stems = {Path(s.path).stem for s in plan.replaced}
    replaced_ids = {s.revision for s in plan.replaced}
    importers = [
        f"  {s.path} imports {name}"
        for s in script_dir.walk_revisions()
        if s.revision not in replaced_ids
        for name in _imported_names(Path(s.path))
        if stems & set(name.split("."))
    ]
    if importers:
        raise SquashError(
            "Revisions after the target import the module of a replaced revision, which the squash deletes:\n"
            + "\n".join(importers)
        )

    found = [f"{s.path}:{line}: {text}" for s in plan.replaced for line, text in _global_statements(Path(s.path))]
    if found and not allow_global_statements:
        raise SquashError(
            "The replaced revisions change objects of the whole server, which pg_dump does not dump:\n"
            + "\n".join(f"  {f}" for f in found)
            + "\nMove these statements out of the migrations, for example into the setup of the cluster. If a match "
            "is not such a statement, pass allow_global_statements=True (--allow-global-statements)."
        )
    return [f"Accepted a statement that changes an object of the whole server: {f}" for f in found]


def _global_statements(path: Path) -> list[tuple[int, str]]:
    """Return the line and text of each statement in ``path`` that changes an object of the whole server.

    Only string literals count, so comments do not.  Docstrings and the values of ``comment`` and ``doc`` keyword
    arguments do not count either.  A match must start a statement: at the start of the string, or after ``;``,
    ``$$``, ``BEGIN``, ``THEN``, ``ELSE``, ``LOOP``, or ``EXECUTE '``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skipped.add(id(first.value))
        elif isinstance(node, ast.keyword) and node.arg in {"comment", "doc"}:
            skipped.add(id(node.value))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped:
            for match in _GLOBAL_STATEMENT.finditer(node.value):
                found.append((node.lineno, " ".join(match.group("statement").split())))
    return found


def _imported_names(path: Path) -> list[str]:
    """Return each module name and imported name in the ``import`` statements of ``path``."""
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.extend(f"{node.module or ''}.{alias.name}".lstrip(".") for alias in node.names)
    return names


def _named_owner_objects(replaced: Sequence[Script]) -> frozenset[str]:
    """Return the bare names of the objects that a replaced revision gives an owner by name.

    ``OWNER TO CURRENT_USER``, ``SESSION_USER``, and ``CURRENT_ROLE`` do not count, because they rely on the role that
    runs the migration.
    """
    names: set[str] = set()
    for script in replaced:
        for node in ast.walk(ast.parse(Path(script.path).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for match in _SOURCE_OWNER_TO.finditer(node.value):
                    if match.group("role").upper() not in {"CURRENT_USER", "SESSION_USER", "CURRENT_ROLE"}:
                        names.add(_bare_name(match.group("name")))
    return frozenset(names)


def _owned_object(statement: str) -> str | None:
    """Return the bare name of the object in a ``pg_dump`` ``ALTER ... OWNER TO`` statement."""
    match = _DUMP_OWNER_OBJECT.match(statement)
    return _bare_name(match.group(1)) if match else None


def _bare_name(name: str) -> str:
    """Return the last part of a possibly qualified name, unquoted, or in lower case when it is not quoted."""
    last = re.split(r'\.(?=(?:[^"]*"[^"]*")*[^"]*$)', name.split("(")[0].strip())[-1]
    return _unquote(last) if last.startswith('"') else last.lower()


def _touched_tables(statements: Sequence[str]) -> frozenset[str]:
    """Return the bare names of the tables that ``extra_sql`` statements read or write."""
    names: set[str] = set()
    for statement in statements:
        try:
            names.update(_bare_name(name) for name in postgast.extract_tables(postgast.parse(statement)))
        except postgast.PgQueryError:
            continue
    return frozenset(names)


def _carried_extra(replaced: Sequence[Script]) -> list[str]:
    """Return the ``EXTRA_STATEMENTS`` of earlier squashed revisions in the replaced set, in chain order."""
    carried: list[str] = []
    for script in replaced:
        carried.extend(getattr(script.module, "EXTRA_STATEMENTS", ()))
    return carried


def _split_extra(sql: str | None) -> list[str]:
    if not sql:
        return []
    try:
        raw = postgast.split(sql)
    except postgast.PgQueryError as e:
        raise SquashError(f"postgast cannot split extra_sql: {e}") from e
    return [statement for statement in (_strip_leading_comments(r) for r in raw) if statement]


def _declared_labels(script: Script) -> tuple[str, ...]:
    """Return the branch labels that the revision file declares, without the labels that Alembic propagates."""
    return tuple(sorted(_as_tuple(getattr(script.module, "branch_labels", None))))


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


class _VersionTable(NamedTuple):
    name: str
    schema: str

    @property
    def pattern(self) -> str:
        """Return a ``pg_dump`` pattern that matches the table literally, without case folding."""
        return f"{_quote_pattern(self.schema)}.{_quote_pattern(self.name)}"


def _quote_pattern(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class _ScratchServer:
    """Create scratch databases on one server, watch its global objects, and clean up at the end."""

    def __init__(self, admin: Engine) -> None:
        self._admin: Final = admin
        self._token: Final = uuid.uuid4().hex[:12]
        self._created: list[str] = []
        with admin.connect() as conn:
            self.version: Final = str(conn.execute(sa.text("SHOW server_version")).scalar_one())
            self.role: Final = str(conn.execute(sa.text("SELECT current_user")).scalar_one())
            self.settings: Final = frozenset(conn.execute(sa.text("SELECT name FROM pg_settings")).scalars())
        self._before: Final = self._global_state()

    def create(self, suffix: str) -> URL:
        name = f"squash_{self._token}_{suffix}"
        try:
            with self._admin.connect() as conn:
                conn.execute(sa.text(f"CREATE DATABASE {self._quote(name)} TEMPLATE template0"))
        except DBAPIError as e:
            raise SquashError(f"Cannot create the scratch database {name}: {e.orig}") from e
        self._created.append(name)
        return self._admin.url.set(database=name)

    def check_globals(self, source: str) -> None:
        """Raise when the server's global objects differ from the state before the squash started.

        This is a second check behind the statement scan.  The squash never drops a role or a tablespace, because a
        difference of the server state cannot tell which session created it.
        """
        after = self._global_state()
        added = sorted(after.rows - self._before.rows)
        removed = sorted(self._before.rows - after.rows)
        if added or removed:
            lines = [f"  + {row}" for row in added] + [f"  - {row}" for row in removed]
            raise SquashError(
                f"During {source}, objects of the whole server changed:\n"
                + "\n".join(lines)
                + "\npg_dump does not dump these, so the new revision cannot recreate them. Another session can also "
                "cause this difference. The changes remain on the server. Remove them yourself."
            )

    def close(self) -> None:
        """Drop the scratch databases.  Roles and tablespaces stay."""
        try:
            with self._admin.connect() as conn:
                for name in self._created:
                    conn.execute(sa.text(f"DROP DATABASE IF EXISTS {self._quote(name)} WITH (FORCE)"))
                self._created.clear()
        finally:
            self._admin.dispose()

    def _quote(self, name: str) -> str:
        return self._admin.dialect.identifier_preparer.quote(name)

    def _global_state(self) -> _GlobalState:
        with self._admin.connect() as conn:
            rows = frozenset(conn.execute(sa.text(_GLOBAL_STATE_QUERY)).scalars())
        return _GlobalState(rows)


class _GlobalState(NamedTuple):
    rows: frozenset[str]


class _Dumper(NamedTuple):
    binary: str
    extra: tuple[str, ...]

    def dump(self, url: URL, *args: str) -> str:
        """Return ``pg_dump --schema-only`` output for the database that ``url`` names, with owners."""
        argv = [self.binary, "--schema-only", *self.extra, *args, f"--dbname={_libpq_uri(url)}"]
        env = dict(os.environ, PGCLIENTENCODING="UTF8")
        if url.password is not None:
            env["PGPASSWORD"] = str(url.password)
        try:
            done = subprocess.run(argv, check=False, capture_output=True, env=env)
        except OSError as e:
            raise SquashError(f"Cannot run {self.binary}: {e}") from e
        if done.returncode != 0:
            stderr = done.stderr.decode("utf-8", errors="replace").strip()
            raise SquashError(f"{self.binary} failed with status {done.returncode}: {stderr}")
        return done.stdout.decode("utf-8")


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


class _ScriptCopy:
    """A copy of the script directory and of each version location, with a configuration that loads the copy."""

    def __init__(self, config: Config, script_dir: ScriptDirectory, tmp: Path) -> None:
        root = Path(script_dir.dir).resolve()
        copy_root = tmp / "script"
        shutil.copytree(root, copy_root, ignore=shutil.ignore_patterns("__pycache__"))
        self._places: list[tuple[Path, Path]] = []
        locations: list[Path] = []
        for index, location in enumerate(script_dir.version_locations or [str(root / "versions")]):
            source = Path(location).resolve()
            if source.is_relative_to(root):
                copied = copy_root / source.relative_to(root)
            else:
                copied = tmp / f"versions_{index}"
                shutil.copytree(source, copied, ignore=shutil.ignore_patterns("__pycache__"))
            self._places.append((source, copied))
            locations.append(copied)
        self._places.append((root, copy_root))
        self._places.sort(key=lambda place: len(place[0].parts), reverse=True)
        self.locations: Final = tuple(locations)

        self.config: Final = _scratch_config(config, None)
        _set_raw(self.config, "script_location", str(copy_root))
        if config.get_version_locations_list() is not None:
            _set_raw(self.config, "path_separator", "newline")
            _set_raw(self.config, "version_locations", "\n".join(str(p) for p in locations))
            prepend = config.get_prepend_sys_paths_list()
            if prepend:
                _set_raw(self.config, "prepend_sys_path", "\n".join(prepend))

    def map(self, path: Path) -> Path:
        """Return the copy of ``path``, through the version location or script directory that holds it."""
        resolved = path.resolve()
        for source, copied in self._places:
            if resolved.is_relative_to(source):
                return copied / resolved.relative_to(source)
        raise SquashError(f"{path} is outside the script directory and every version location")


def _check_copy(config: Config, plan: _Plan, revision_copy: Path) -> None:
    """Check that the copied chain loads the new revision as the base and no replaced revision."""
    script_dir = ScriptDirectory.from_config(config)
    loaded = {s.revision: s for s in script_dir.walk_revisions()}
    target = loaded.get(plan.target.revision)
    leftover = sorted(s.revision for s in plan.replaced if s.revision != plan.target.revision and s.revision in loaded)
    if (
        target is None
        or target.down_revision is not None
        or Path(target.path).resolve() != revision_copy.resolve()
        or leftover
    ):
        raise SquashError(
            f"The copy of the script directory does not load the new revision as expected. Leftover revisions: "
            f"{leftover}. Report this as a bug."
        )


def _preflight(config: Config, script_dir: ScriptDirectory, url: URL, tmp: Path) -> None:
    """Run ``env.py`` with one sentinel revision, and stop unless it ran in the scratch database and nowhere else."""
    copy = _ScriptCopy(config, script_dir, tmp)
    for script in script_dir.walk_revisions():
        copy.map(Path(script.path)).unlink()
    trip = tmp / "tripped"
    (copy.locations[0] / "alembic_pg_autogen_squash_preflight.py").write_text(
        _PREFLIGHT_TEMPLATE.format(expected=repr(url.database), marker=repr(_PREFLIGHT_MARKER), trip=repr(str(trip))),
        encoding="utf-8",
    )
    hint = "env.py must connect to the scratch database, and only to it. It must read the sqlalchemy.url option or "
    hint += "use config.attributes['connection']."
    connected: list[str] = []

    def record(conn: sa.Connection) -> None:
        connected.append(str(conn.engine.url.database))

    event.listen(sa.Engine, "engine_connect", record)
    try:
        _run_upgrade(copy.config, url, "heads")
    except Exception as e:
        raise SquashError(f"The preflight failed, so the squash ran no revision of the chain: {e}\n{hint}") from e
    finally:
        event.remove(sa.Engine, "engine_connect", record)
    if others := sorted(set(connected) - {str(url.database)}):
        raise SquashError(
            f"During the preflight, env.py also connected to {others}, so the squash ran no revision of the chain. "
            f"env.py runs the migrations in more than one database.\n{hint}"
        )
    if trip.exists():
        raise SquashError(
            f"The preflight sentinel also ran in the database {trip.read_text(encoding='utf-8')!r}, so the squash ran "
            f"no revision of the chain. env.py runs the migrations in more than one database.\n{hint}"
        )
    engine = sa.create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            marker = conn.execute(sa.text("SELECT to_regclass(:name)"), {"name": _PREFLIGHT_MARKER}).scalar()
    finally:
        engine.dispose()
    if marker is None:
        raise SquashError(
            f"The preflight sentinel did not run in the scratch database {url.database}, so the squash ran no "
            f"revision of the chain. env.py may have caught the sentinel's error.\n{hint}"
        )


def _upgrade(
    config: Config, url: URL, revision: str, version: _VersionTable, *, expected: frozenset[str] | None = None
) -> frozenset[str]:
    """Run ``alembic upgrade`` against one scratch database and return the rows of its version table."""
    found = _run_upgrade(config, url, revision, version)
    if expected is not None and found != expected:
        raise SquashError(
            f"After 'upgrade {revision}', the scratch database {url.database} holds versions {sorted(found)}, not "
            f"{sorted(expected)}. version_table and version_table_schema must match the arguments of squash(), and "
            f"the target must be the only head after the upgrade."
        )
    return found


def _upgrade_new(
    config: Config, url: URL, revision: str, version: _VersionTable, *, expected: frozenset[str] | None = None
) -> frozenset[str]:
    """Run ``_upgrade`` with the new chain, and report a database error as ``SquashError``."""
    try:
        return _upgrade(config, url, revision, version, expected=expected)
    except SquashError:
        raise
    except Exception as e:
        raise SquashError(f"The new chain fails at 'upgrade {revision}' on a fresh database: {e}") from e


def _run_upgrade(config: Config, url: URL, revision: str, version: _VersionTable | None = None) -> frozenset[str]:
    engine = sa.create_engine(url, poolclass=NullPool)
    try:
        scratch_config = _scratch_config(config, url)
        with engine.connect() as conn:
            scratch_config.attributes["connection"] = conn
            command.upgrade(scratch_config, revision)
            conn.commit()
        return _read_versions(engine, version) if version is not None else frozenset()
    finally:
        engine.dispose()


def _read_versions(engine: Engine, version: _VersionTable) -> frozenset[str]:
    quote = engine.dialect.identifier_preparer.quote
    qualified = f"{quote(version.schema)}.{quote(version.name)}"
    with engine.connect() as conn:
        exists = conn.execute(sa.text("SELECT to_regclass(:name)"), {"name": version.pattern})
        if exists.scalar() is None:
            return frozenset()
        return frozenset(conn.execute(sa.text(f"SELECT version_num FROM {qualified}")).scalars())


def _scratch_config(config: Config, url: URL | None) -> Config:
    """Return a copy of ``config``.  With ``url``, its ``sqlalchemy.url`` names that database."""
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
    if config.file_config.has_section(section):
        for key, value in config.file_config.items(section, raw=True):
            copy.file_config.set(section, key, value)
    if url is not None:
        _set_raw(copy, "sqlalchemy.url", url.render_as_string(hide_password=False))
    return copy


def _set_raw(config: Config, key: str, value: str) -> None:
    config.set_main_option(key, value.replace("%", "%%"))


def _config_files(names: Sequence[str]) -> tuple[str, str]:
    """Pick the ini file and the ``pyproject.toml`` file the way the ``alembic`` command does."""
    env = os.environ.get("ALEMBIC_CONFIG")
    default_ini, default_toml = "alembic.ini", "pyproject.toml"
    if env and os.path.basename(env) == "pyproject.toml":
        default_toml = env
    elif env:
        default_ini = env
    ini = [n for n in names if os.path.basename(n) != "pyproject.toml"]
    toml = [n for n in names if os.path.basename(n) == "pyproject.toml"]
    if len(ini) > 1 or len(toml) > 1:
        raise SquashError("Give at most one ini file and one pyproject.toml")
    return (ini[0] if ini else default_ini), (toml[0] if toml else default_toml)


def _post_statements(url: URL, statements: Sequence[str]) -> tuple[list[str], list[str]]:
    """Plan the materialized view refreshes that run after the settings are restored.

    Each populated materialized view is refreshed, in dependency order.  An unpopulated materialized view that a
    populated one reads is refreshed first and emptied again with ``WITH NO DATA`` at the end.  The ``OWNER TO`` of
    each refreshed view moves after its refresh, because the old chain populated the view as the role that created it.

    Returns:
        The statements without the moved ``OWNER TO`` statements, and the statements that run last.
    """
    engine = sa.create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            names = dict(conn.execute(sa.text(_RELATION_NAMES_QUERY)).tuples().all())
            matviews = dict(conn.execute(sa.text(_MATVIEWS_QUERY)).tuples().all())
            edges = conn.execute(sa.text(_VIEW_DEPENDENCIES_QUERY)).tuples().all()
    finally:
        engine.dispose()
    populated = {oid for oid, is_populated in matviews.items() if is_populated}
    if not populated:
        return list(statements), []

    graph: dict[int, set[int]] = {oid: set() for oid in names}
    for view, referenced in edges:
        graph.setdefault(view, set()).add(referenced)
    needed: set[int] = set()
    pending = list(populated)
    while pending:
        oid = pending.pop()
        if oid not in needed:
            needed.add(oid)
            pending.extend(graph.get(oid, ()))
    try:
        order = list(graphlib.TopologicalSorter({oid: graph.get(oid, set()) & needed for oid in needed}).static_order())
    except graphlib.CycleError as e:
        raise SquashError(f"The views form a cycle, so the squash cannot order the refreshes: {e.args[1]}") from e

    refreshed = [oid for oid in order if oid in matviews]
    refreshed_names = {names[oid] for oid in refreshed}
    kept: list[str] = []
    owners: list[str] = []
    for statement in statements:
        match = _MATVIEW_OWNER_TO.match(statement)
        (owners if match and match.group(1) in refreshed_names else kept).append(statement)
    post = [f"REFRESH MATERIALIZED VIEW {names[oid]}" for oid in refreshed]
    post += [f"REFRESH MATERIALIZED VIEW {names[oid]} WITH NO DATA" for oid in refreshed if oid not in populated]
    return kept, post + owners


def _compare(dumper: _Dumper, old: URL, new: URL, where: str) -> None:
    """Compare the dump text and the catalog properties that the dump does not show."""
    _raise_on_diff(_strip_restrict(dumper.dump(old)), _strip_restrict(dumper.dump(new)), f"schemas {where}")
    _raise_on_diff(_catalog_state(old), _catalog_state(new), f"catalog properties {where}")


def _catalog_state(url: URL) -> str:
    engine = sa.create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return "".join(f"{line}\n" for line in conn.execute(sa.text(_CATALOG_STATE_QUERY)).scalars())
    finally:
        engine.dispose()


def _data_differences(old: URL, new: URL, version: _VersionTable, digest_tables: AbstractSet[str]) -> list[str]:
    """Compare row counts, sequences, and the rows of the tables in ``digest_tables``."""
    old_state, new_state = _data_state(old, version, digest_tables), _data_state(new, version, digest_tables)
    return [
        f"{name}: {old_state.get(name, 'absent')} with the old chain, {new_state.get(name, 'absent')} with the new"
        for name in sorted(old_state.keys() | new_state.keys())
        if old_state.get(name) != new_state.get(name)
    ]


def _data_state(url: URL, version: _VersionTable, digest_tables: AbstractSet[str]) -> Mapping[str, str]:
    engine = sa.create_engine(url, poolclass=NullPool)
    state: dict[str, str] = {}
    try:
        with engine.connect() as conn:
            for name, schema, table in conn.execute(sa.text(_TABLES_QUERY)).tuples():
                if (schema, table) == (version.schema, version.name):
                    continue
                count = conn.execute(sa.text(f"SELECT count(*) FROM {name}")).scalar_one()
                state[f"table {name}"] = f"{count} rows"
                if table in digest_tables:
                    digest = conn.execute(
                        sa.text(f"SELECT md5(coalesce(string_agg(r::text, ',' ORDER BY r::text), '')) FROM {name} r")
                    ).scalar_one()
                    state[f"rows of {name}"] = f"digest {digest}"
            for name in conn.execute(sa.text(_SEQUENCES_QUERY)).scalars():
                last_value, is_called = conn.execute(sa.text(f"SELECT last_value, is_called FROM {name}")).one()
                state[f"sequence {name}"] = f"last_value {last_value} is_called {is_called}"
    finally:
        engine.dispose()
    return state


def _raise_on_diff(old: str, new: str, what: str) -> None:
    if old == new:
        return
    diff = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), "old chain", "new chain", n=2
    )
    lines = list(diff)
    shown = "".join(lines[:_MAX_DIFF_LINES])
    more = f"\n... {len(lines) - _MAX_DIFF_LINES} more diff lines" if len(lines) > _MAX_DIFF_LINES else ""
    raise SquashError(f"The two chains give different {what}:\n{shown}{more}")


def _write(revision_path: Path, source: str, removed_paths: Sequence[Path]) -> None:
    """Write the new revision atomically, then delete the replaced files."""
    temporary = revision_path.with_name(f".{revision_path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as fh:
            fh.write(source)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, revision_path)
    except OSError as e:
        temporary.unlink(missing_ok=True)
        raise SquashError(f"Cannot write {revision_path}: {e}. No file changed.") from e
    remaining: list[str] = []
    for path in removed_paths:
        if path.resolve() == revision_path.resolve():
            continue
        try:
            path.unlink()
        except OSError as e:
            remaining.append(f"{path} ({e})")
    if remaining:
        raise SquashError(
            f"Wrote {revision_path}, but cannot delete these replaced files. Delete them by hand: {', '.join(remaining)}"
        )
    log.info("Wrote %s and removed %d revision files", revision_path, len(removed_paths))


def _strip_restrict(dump: str) -> str:
    r"""Remove the ``\restrict KEY`` wrapper of ``pg_dump`` and nothing else.

    The wrapper counts only when ``\restrict KEY`` is the first line that is not blank and not a comment.  The matching
    ``\unrestrict KEY`` counts only as the last such line.  An older ``pg_dump`` writes no wrapper, and a function body
    can hold the same text.
    """
    lines = dump.split("\n")
    first = next((i for i, line in enumerate(lines) if _is_content(line)), None)
    if first is None or not (match := _RESTRICT_LINE.fullmatch(lines[first].strip())):
        return dump
    del lines[first]
    last = next((i for i in range(len(lines) - 1, -1, -1) if _is_content(lines[i])), None)
    if last is not None and lines[last].strip() == f"\\unrestrict {match.group(1)}":
        del lines[last]
    return "\n".join(lines)


def _is_content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("--")


def _strip_leading_comments(statement: str) -> str:
    """Remove the blank lines and ``--`` comment lines before a statement, and nothing inside it."""
    lines = statement.strip().split("\n")
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    return "\n".join(lines).strip()


def _setting_name(statement: str) -> str | None:
    match = _SETTING.match(statement)
    return (match.group(1) or match.group(2)) if match else None


def _unquote(identifier: str) -> str:
    if identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier


_MAX_DIFF_LINES: Final = 200
_RESTRICT_LINE: Final = re.compile(r"\\restrict (\S+)")
_CLIENT_ENCODING: Final = re.compile(r"SET\s+client_encoding\b", re.IGNORECASE)
_COMMENT_ON_EXTENSION: Final = re.compile(r"COMMENT\s+ON\s+EXTENSION\b", re.IGNORECASE)
_SETTING: Final = re.compile(r"SET\s+(\w+)\s*(?:=|TO)|SELECT\s+pg_catalog\.set_config\('(\w+)'", re.IGNORECASE)
_IDENTIFIER: Final = r'("(?:[^"]|"")+"|[^\s";]+)'
_OWNER_TO: Final = re.compile(rf"ALTER\s.+\sOWNER\s+TO\s+{_IDENTIFIER}\s*$", re.IGNORECASE | re.DOTALL)
_CREATE_SCHEMA: Final = re.compile(rf"CREATE\s+SCHEMA\s+{_IDENTIFIER}\s*$", re.IGNORECASE)
_DEFAULT_PRIVILEGES_FOR_ROLE: Final = re.compile(
    rf"(ALTER\s+DEFAULT\s+PRIVILEGES\s+)FOR\s+ROLE\s+{_IDENTIFIER}\s+", re.IGNORECASE
)
# ``pg_dump`` 14 writes ``ALTER TABLE`` for a materialized view, newer versions write ``ALTER MATERIALIZED VIEW``.
_MATVIEW_OWNER_TO: Final = re.compile(
    rf"ALTER\s+(?:MATERIALIZED\s+VIEW|TABLE)\s+(\S+)\s+OWNER\s+TO\s+{_IDENTIFIER}\s*$", re.I
)
_NAME: Final = r'(?:"(?:[^"]|"")+"|[\w$]+)'
_SOURCE_OWNER_TO: Final = re.compile(
    rf"\bALTER\s+[A-Za-z ]+?\s+(?:IF\s+EXISTS\s+)?(?P<name>{_NAME}(?:\.{_NAME})?)\s*(?:\([^)]*\))?\s+"
    r'OWNER\s+TO\s+"?(?P<role>\w+)',
    re.IGNORECASE,
)
_DUMP_OWNER_OBJECT: Final = re.compile(
    rf"ALTER\s+[A-Za-z ]+?\s+({_NAME}(?:\.{_NAME})?(?:\(.*\))?)\s+OWNER\s+TO\s", re.IGNORECASE | re.DOTALL
)
_ROLE_LIST: Final = rf"{_NAME}(?:\s*,\s*{_NAME})*"
_STATEMENT_END: Final = r"\s*(?:;|\Z|\$\$|'|\bEND\b)"
_GLOBAL_STATEMENT: Final = re.compile(
    r"(?:\A|;|\$\$|\b(?:BEGIN|THEN|ELSE|LOOP)\b|\bEXECUTE\s+(?:format\s*\(\s*)?')\s*(?P<statement>"
    rf"(?:CREATE|ALTER|DROP)\s+(?:ROLE|GROUP|TABLESPACE|USER(?!\s+MAPPING))\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?{_NAME}"
    rf"|ALTER\s+DATABASE\s+{_NAME}"
    r"|ALTER\s+SYSTEM\s+(?:SET|RESET)\b"
    rf"|GRANT\s+{_ROLE_LIST}\s+TO\s+{_ROLE_LIST}(?:\s+WITH\s+\w+\s+OPTION)?(?:\s+GRANTED\s+BY\s+{_NAME})?(?={_STATEMENT_END})"
    rf"|REVOKE\s+(?:ADMIN\s+OPTION\s+FOR\s+)?{_ROLE_LIST}\s+FROM\s+{_ROLE_LIST}(?:\s+(?:CASCADE|RESTRICT))?"
    rf"(?={_STATEMENT_END})"
    r")",
    re.IGNORECASE,
)
_PREFLIGHT_MARKER: Final = "public.alembic_pg_autogen_squash_preflight"

_USER_RELATIONS: Final = """
    n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_toast%'
    AND n.nspname NOT LIKE 'pg\\_temp\\_%'
"""
_CATALOG_STATE_QUERY: Final = f"""
    SELECT format('columns %I.%I: %s', n.nspname, c.relname,
                  (SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) FROM pg_attribute a
                   WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped))
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND {_USER_RELATIONS}
    UNION ALL
    SELECT format('materialized view %I.%I populated %s', n.nspname, c.relname, c.relispopulated)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'm' AND {_USER_RELATIONS}
    ORDER BY 1
"""
_TABLES_QUERY: Final = f"""
    SELECT format('%I.%I', n.nspname, c.relname), n.nspname, c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'm') AND {_USER_RELATIONS} AND (c.relkind <> 'm' OR c.relispopulated)
    ORDER BY 1
"""
_SEQUENCES_QUERY: Final = f"""
    SELECT format('%I.%I', n.nspname, c.relname)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'S' AND {_USER_RELATIONS}
    ORDER BY 1
"""
_RELATION_NAMES_QUERY: Final = f"""
    SELECT c.oid::int, format('%I.%I', n.nspname, c.relname)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('v', 'm') AND {_USER_RELATIONS}
"""
_MATVIEWS_QUERY: Final = f"""
    SELECT c.oid::int, c.relispopulated FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'm' AND {_USER_RELATIONS}
"""
_VIEW_DEPENDENCIES_QUERY: Final = """
    SELECT DISTINCT r.ev_class::int, d.refobjid::int
    FROM pg_rewrite r
    JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = r.oid AND d.refclassid = 'pg_class'::regclass
    JOIN pg_class ref ON ref.oid = d.refobjid AND ref.relkind IN ('v', 'm')
    WHERE r.rulename = '_RETURN' AND d.refobjid <> r.ev_class
"""
_GLOBAL_STATE_QUERY: Final = """
    SELECT format('role %I super=%s inherit=%s createrole=%s createdb=%s login=%s replication=%s bypassrls=%s '
                  'connlimit=%s', rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin,
                  rolreplication, rolbypassrls, rolconnlimit)
    FROM pg_roles
    UNION ALL
    SELECT format('member %s of %s', m.member::regrole, m.roleid::regrole) FROM pg_auth_members m
    UNION ALL
    SELECT format('tablespace %I', spcname) FROM pg_tablespace
    UNION ALL
    SELECT format('setting database=%s role=%s %s', coalesce(d.datname, '*'), coalesce(r.rolname, '*'),
                  array_to_string(s.setconfig, ', '))
    FROM pg_db_role_setting s
    LEFT JOIN pg_database d ON d.oid = s.setdatabase
    LEFT JOIN pg_roles r ON r.oid = s.setrole
"""

_PREFLIGHT_TEMPLATE: Final = '''\
"""Sentinel revision of the alembic_pg_autogen squash preflight."""

import sqlalchemy as sa
from alembic import op

revision = "squash_preflight"
down_revision = None
branch_labels = None
depends_on = None

EXPECTED = {expected}
TRIP = {trip}


def upgrade():
    found = op.get_bind().execute(sa.text("SELECT current_database()")).scalar()
    if found != EXPECTED:
        with open(TRIP, "w", encoding="utf-8") as trip:
            trip.write(str(found))
        raise RuntimeError(f"env.py connected to the database {{found!r}}, not to the scratch database {{EXPECTED!r}}")
    op.execute(sa.text("CREATE TABLE " + {marker} + " (ok int)"))


def downgrade():
    pass
'''

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
EXTRA_STATEMENTS = (
{extra})
POST_STATEMENTS = (
{post})


def upgrade():
    for name in SETTINGS:
        op.execute(
            sa.DDL(f"SELECT set_config('alembic_pg_autogen.squash_{{name}}', current_setting('{{name}}', true), false)")
        )
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement.replace("%", "%%")))
    for name in SETTINGS:
        op.execute(
            sa.DDL(
                f"SELECT set_config('{{name}}', current_setting('alembic_pg_autogen.squash_{{name}}'), false) "
                f"WHERE current_setting('{{name}}', true) IS NOT NULL"
            )
        )
    for statement in EXTRA_STATEMENTS + POST_STATEMENTS:
        op.execute(sa.DDL(statement.replace("%", "%%")))


def downgrade():
    raise NotImplementedError("{target} squashes {count} revisions and cannot be downgraded")
'''


if __name__ == "__main__":
    raise SystemExit(main())
